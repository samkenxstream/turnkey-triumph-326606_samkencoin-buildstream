#!/usr/bin/env python3
#
#  Copyright (C) 2016 Codethink Limited
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 2 of the License, or (at your option) any later version.
#
#  This library is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	 See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library. If not, see <http://www.gnu.org/licenses/>.
#
#  This module was forked from the python click library.

import collections
import copy
import os
import sys
import re
import click

from click.parser import split_arg_string
from click.core import MultiCommand, Option, Argument

WORDBREAK = '='

COMPLETION_SCRIPT = '''
%(complete_func)s() {
    local IFS=$'\n'
    COMPREPLY=( $( env COMP_WORDS="${COMP_WORDS[*]}" \\
                   COMP_CWORD=$COMP_CWORD \\
                   %(autocomplete_var)s=complete $1 ) )
    return 0
}

complete -F %(complete_func)s -o nospace %(script_names)s
'''

_invalid_ident_char_re = re.compile(r'[^a-zA-Z0-9_]')


def _complete_path(path_type, incomplete):
    """Helper method for implementing the completions() method
    for File and Path parameter types.
    """

    # Try listing the files in the relative or absolute path
    # specified in `incomplete` minus the last path component,
    # otherwise list files starting from the current working directory.
    entries = []
    base_path = ''
    if os.path.sep in incomplete:
        split = incomplete.rsplit(os.path.sep, 1)
        base_path = split[0]

        # If there was nothing on the left of the last separator,
        # we are completing files in the filesystem root
        if not base_path:
            base_path = os.path.sep

    try:
        if base_path:
            if os.path.isdir(base_path):
                entries = [os.path.join(base_path, e) for e in os.listdir(base_path)]
        else:
            entries = os.listdir(".")
    except OSError:
        # If for any reason the os reports an error from os.listdir(), just
        # ignore this and avoid a stack trace
        pass

    return [
        # Append slashes to any entries which are directories, or
        # spaces for other files since they cannot be further completed
        e + os.path.sep if os.path.isdir(e) else e + " "

        for e in sorted(entries)

        # Filter out non directory elements when searching for a directory,
        # the opposite is fine, however.
        if not (path_type == 'Directory' and not os.path.isdir(e))
    ]


# Instead of delegating completions to the param type,
# hard code all of buildstream's completions here.
#
# This whole module should be removed in favor of more
# generic code in click once this issue is resolved:
#   https://github.com/pallets/click/issues/780
#
def get_param_type_completion(param_type, incomplete):

    if isinstance(param_type, click.Choice):
        return [c + " " for c in param_type.choices]
    elif isinstance(param_type, click.BoolParamType):
        return ["yes ", "no "]
    elif isinstance(param_type, click.File):
        return _complete_path("File", incomplete)
    elif isinstance(param_type, click.Path):
        return _complete_path(param_type.path_type, incomplete)

    return []


def get_completion_script(prog_name, complete_var):
    return (COMPLETION_SCRIPT % {
        'complete_func': '_bst_completion',
        'script_names': 'bst',
        'autocomplete_var': complete_var,
    }).strip() + ';'


def resolve_ctx(cli, prog_name, args):
    """
    Parse into a hierarchy of contexts. Contexts are connected through the parent variable.
    :param cli: command definition
    :param prog_name: the program that is running
    :param args: full list of args
    :return: the final context/command parsed
    """
    ctx = cli.make_context(prog_name, args, resilient_parsing=True)
    args_remaining = ctx.protected_args + ctx.args
    while ctx is not None and args_remaining:
      if isinstance(ctx.command, MultiCommand):
        cmd = ctx.command.get_command(ctx, args_remaining[0])
        if cmd is None:
            return None
        ctx = cmd.make_context(args_remaining[0], args_remaining[1:], parent=ctx, resilient_parsing=True)
        args_remaining = ctx.protected_args + ctx.args
      else:
        ctx = ctx.parent

    return ctx


def start_of_option(param_str):
    """
    :param param_str: param_str to check
    :return: whether or not this is the start of an option declaration (i.e. starts "-" or "--")
    """
    return param_str and param_str[:1] == '-'


def is_incomplete_option(all_args, cmd_param):
    """
    :param all_args: the full original list of args supplied
    :param cmd_param: the current command paramter
    :return: whether or not the last option declaration (i.e. starts "-" or "--") is incomplete and
    corresponds to this cmd_param. In other words whether this cmd_param option can still accept
    values
    """
    if cmd_param.is_flag:
        return False
    last_option = None
    for index, arg_str in enumerate(reversed([arg for arg in all_args if arg != WORDBREAK])):
        if index + 1 > cmd_param.nargs:
            break
        if start_of_option(arg_str):
            last_option = arg_str

    return True if last_option and last_option in cmd_param.opts else False


def is_incomplete_argument(current_params, cmd_param):
    """
    :param current_params: the current params and values for this argument as already entered
    :param cmd_param: the current command parameter
    :return: whether or not the last argument is incomplete and corresponds to this cmd_param. In
    other words whether or not the this cmd_param argument can still accept values
    """
    current_param_values = current_params[cmd_param.name]
    if current_param_values is None:
        return True
    if cmd_param.nargs == -1:
        return True
    if isinstance(current_param_values, collections.Iterable) \
            and cmd_param.nargs > 1 and len(current_param_values) < cmd_param.nargs:
        return True
    return False


def get_user_autocompletions(ctx, args, incomplete, cmd_param):
    """
    :param ctx: context associated with the parsed command
    :param args: full list of args
    :param incomplete: the incomplete text to autocomplete
    :param cmd_param: command definition
    :return: all the possible user-specified completions for the param
    """
    if cmd_param.autocompletion is not None:
        return cmd_param.autocompletion(ctx=ctx,
                                        args=args,
                                        incomplete=incomplete)
    else:
        return cmd_param.type.completions(incomplete) or []


def get_choices(cli, prog_name, args, incomplete):
    """
    :param cli: command definition
    :param prog_name: the program that is running
    :param args: full list of args
    :param incomplete: the incomplete text to autocomplete
    :return: all the possible completions for the incomplete
    """
    all_args = copy.deepcopy(args)

    ctx = resolve_ctx(cli, prog_name, args)
    if ctx is None:
        return

    # In newer versions of bash long opts with '='s are partitioned, but it's easier to parse
    # without the '='
    if start_of_option(incomplete) and WORDBREAK in incomplete:
        partition_incomplete = incomplete.partition(WORDBREAK)
        all_args.append(partition_incomplete[0])
        incomplete = partition_incomplete[2]
    elif incomplete == WORDBREAK:
        incomplete = ''

    choices = []
    found_param = False
    if start_of_option(incomplete):
        # completions for options
        for param in ctx.command.params:
            if isinstance(param, Option):
                choices.extend([param_opt + " " for param_opt in param.opts + param.secondary_opts
                                if param_opt not in all_args or param.multiple])
        found_param = True
    if not found_param:
        # completion for option values by choices
        for cmd_param in ctx.command.params:
            if isinstance(cmd_param, Option) and is_incomplete_option(all_args, cmd_param):
                choices.extend(get_user_autocompletions(ctx, all_args, incomplete, cmd_param))
                found_param = True
                break
    if not found_param:
        # completion for argument values by choices
        for cmd_param in ctx.command.params:
            if isinstance(cmd_param, Argument) and is_incomplete_argument(ctx.params, cmd_param):
                choices.extend(get_user_autocompletions(ctx, all_args, incomplete, cmd_param))
                found_param = True
                break

    if not found_param and isinstance(ctx.command, MultiCommand):
        # completion for any subcommands
        choices.extend([cmd + " " for cmd in ctx.command.list_commands(ctx)])

    if not start_of_option(incomplete) and ctx.parent is not None and isinstance(ctx.parent.command, MultiCommand) and ctx.parent.command.chain:
        # completion for chained commands
        remaining_comands = set(ctx.parent.command.list_commands(ctx.parent))-set(ctx.parent.protected_args)
        choices.extend([cmd + " " for cmd in remaining_comands])

    for item in choices:
        if item.startswith(incomplete):
            yield item


def do_complete(cli, prog_name):
    cwords = split_arg_string(os.environ['COMP_WORDS'])
    cword = int(os.environ['COMP_CWORD'])
    args = cwords[1:cword]
    try:
        incomplete = cwords[cword]
    except IndexError:
        incomplete = ''

    for item in get_choices(cli, prog_name, args, incomplete):
        click.echo(item)

    return True


def bashcomplete(cli, prog_name, complete_instr):
    if complete_instr == 'source':
        click.echo(get_completion_script(prog_name, '_BST_COMPLETION'))
        return True
    elif complete_instr == 'complete':
        return do_complete(cli, prog_name)
    return False


def fast_exit(code):
    """Exit without garbage collection, this speeds up exit by about 10ms for
    things like bash completion.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


# Main function called from main.py at startup here
#
def main_bashcomplete(cmd, prog_name):
    """Internal handler for the bash completion support."""
    complete_instr = os.environ.get('_BST_COMPLETION')
    
    if not complete_instr:
        return

    if bashcomplete(cmd, prog_name, complete_instr):
        fast_exit(1)
