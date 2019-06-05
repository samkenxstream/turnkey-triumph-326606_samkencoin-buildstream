# Pylint doesn't play well with fixtures and dependency injection from pytest
# pylint: disable=redefined-outer-name

import os
import pytest

from buildstream.testing import cli_integration as cli  # pylint: disable=unused-import
from tests.testutils.site import HAVE_SANDBOX


pytestmark = pytest.mark.integration


DATA_DIR = os.path.join(
    os.path.dirname(os.path.realpath(__file__)),
    "project"
)


@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.skipif(not HAVE_SANDBOX, reason='Only available with a functioning sandbox')
def test_absolute_symlinks(cli, datafiles):
    project = str(datafiles)
    checkout = os.path.join(cli.directory, 'checkout')
    element_name = 'symlinks/dangling-symlink.bst'

    result = cli.run(project=project, args=['build', element_name])
    assert result.exit_code == 0

    result = cli.run(project=project, args=['artifact', 'checkout', element_name, '--directory', checkout])
    assert result.exit_code == 0

    symlink = os.path.join(checkout, 'opt', 'orgname')
    assert os.path.islink(symlink)

    # The symlink is created to point to /usr/orgs/orgname and BuildStream
    # should not mangle symlinks.
    assert os.readlink(symlink) == '/usr/orgs/orgname'


@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.skipif(not HAVE_SANDBOX, reason='Only available with a functioning sandbox')
def test_disallow_overlaps_inside_symlink_with_dangling_target(cli, datafiles):
    project = str(datafiles)
    checkout = os.path.join(cli.directory, 'checkout')
    element_name = 'symlinks/dangling-symlink-overlap.bst'

    result = cli.run(project=project, args=['build', element_name])
    assert result.exit_code == 0

    result = cli.run(project=project, args=['artifact', 'checkout', element_name, '--directory', checkout])
    assert result.exit_code == -1
    assert 'Destination is a symlink, not a directory: /opt/orgname' in result.stderr


@pytest.mark.datafiles(DATA_DIR)
@pytest.mark.skipif(not HAVE_SANDBOX, reason='Only available with a functioning sandbox')
def test_detect_symlink_overlaps_pointing_outside_sandbox(cli, datafiles):
    project = str(datafiles)
    checkout = os.path.join(cli.directory, 'checkout')
    element_name = 'symlinks/symlink-to-outside-sandbox-overlap.bst'

    # Building the two elements should succeed...
    result = cli.run(project=project, args=['build', element_name])
    assert result.exit_code == 0

    # ...but when we compose them together, the overlaps create paths that
    # point outside the sandbox which BuildStream needs to detect before it
    # tries to actually write there.
    result = cli.run(project=project, args=['artifact', 'checkout', element_name, '--directory', checkout])
    assert result.exit_code == -1
    assert 'Destination is a symlink, not a directory: /opt/escape-hatch' in result.stderr
