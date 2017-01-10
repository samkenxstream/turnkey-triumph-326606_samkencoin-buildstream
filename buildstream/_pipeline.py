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
#  Authors:
#        Tristan Van Berkom <tristan.vanberkom@codethink.co.uk>
#        Jürg Billeter <juerg.billeter@codethink.co.uk>

import os
from pluginbase import PluginBase

from ._artifactcache import ArtifactCache
from ._elementfactory import ElementFactory
from ._loader import Loader
from ._sourcefactory import SourceFactory
from . import Scope
from . import _yaml


# The Resolver class instantiates plugin-provided Element and Source classes
# from MetaElement and MetaSource objects
class Resolver():
    def __init__(self, context, project, artifacts, element_factory, source_factory):
        self.context = context
        self.project = project
        self.artifacts = artifacts
        self.element_factory = element_factory
        self.source_factory = source_factory
        self.resolved_elements = {}

    def resolve_element(self, meta_element):
        if meta_element in self.resolved_elements:
            return self.resolved_elements[meta_element]

        element = self.element_factory.create(meta_element.kind,
                                              self.context,
                                              self.project,
                                              self.artifacts,
                                              meta_element)

        self.resolved_elements[meta_element] = element

        # resolve dependencies
        for dep in meta_element.dependencies:
            element._Element__runtime_dependencies.append(self.resolve_element(dep))
        for dep in meta_element.build_dependencies:
            element._Element__build_dependencies.append(self.resolve_element(dep))

        # resolve sources
        for meta_source in meta_element.sources:
            element._Element__sources.append(self.resolve_source(meta_source))

        return element

    def resolve_source(self, meta_source):
        source = self.source_factory.create(meta_source.kind, self.context, self.project, meta_source)

        return source


# Pipeline()
#
# Args:
#    context (Context): The Context object
#    project (Project): The Project object
#    target (str): A bst filename relative to the project directory
#    target_variant (str): The selected variant of 'target', or None for the default
#
# Raises:
#    LoadError
#    PluginError
#    SourceError
#    ElementError
#    ProgramNotFoundError
#
class Pipeline():

    def __init__(self, context, project, target, target_variant):
        self.context = context
        self.project = project
        self.artifactcache = ArtifactCache(self.context)

        pluginbase = PluginBase(package='buildstream.plugins')
        self.element_factory = ElementFactory(pluginbase, project._plugin_element_paths)
        self.source_factory = SourceFactory(pluginbase, project._plugin_source_paths)

        loader = Loader(self.project.directory, target, target_variant, context.arch)
        meta_element = loader.load()

        resolver = Resolver(self.context,
                            self.project,
                            self.artifactcache,
                            self.element_factory,
                            self.source_factory)
        self.target = resolver.resolve_element(meta_element)

        # Preflight right away, after constructing the tree
        for plugin in self.dependencies(Scope.ALL, include_sources=True):
            plugin.preflight()

    # Generator function to iterate over elements and optionally
    # also iterate over sources.
    #
    def dependencies(self, scope, include_sources=False):
        for element in self.target.dependencies(scope):
            if include_sources:
                for source in element._Element__sources:
                    yield source
            yield element

    # inconsistent()
    #
    # Reports a list of inconsistent sources.
    #
    # If a pipeline has inconsistent sources, it must
    # be refreshed before cache keys can be calculated
    # or anything else.
    #
    def inconsistent(self):
        sources = []
        for elt in self.target.dependencies(Scope.ALL):
            sources += elt._inconsistent()
        return sources

    # refresh()
    #
    # Refreshes all the sources of all the elements in the pipeline,
    # i.e. all of the elements which the target somehow depends on.
    #
    # Returns:
    #    (list): The Source objects which have changed due to the refresh
    #
    # If no error is encountered while refreshing, then the project files
    # are rewritten inline.
    #
    def refresh(self):

        files = {}
        sources = []
        for elt in self.target.dependencies(Scope.ALL):
            elt_files, elt_sources = elt._refresh()
            sources += elt_sources
            files.update(elt_files)

        for filename, toplevel in files.items():
            fullname = os.path.join(self.project.directory, filename)
            _yaml.dump(toplevel, fullname)

        return sources
