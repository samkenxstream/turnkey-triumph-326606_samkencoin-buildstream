#
#  Copyright (C) 2019 Bloomberg Finance LP
#  Copyright (C) 2020 Codethink Limited
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

import os
from typing import Optional, cast
from urllib.parse import urlparse
import grpc
from grpc import ChannelCredentials, Channel

from ._exceptions import LoadError, RemoteError
from .exceptions import LoadErrorReason
from .types import FastEnum
from .node import MappingNode


# RemoteType():
#
# Defines the different types of remote.
#
class RemoteType(FastEnum):
    INDEX = "index"
    STORAGE = "storage"
    ENDPOINT = "endpoint"
    ALL = "all"

    def __str__(self) -> str:
        if self.name:
            return self.name.lower().replace("_", "-")
        return ""


# RemoteSpec():
#
# This data structure holds all of the details required to
# connect to and communicate with the various grpc remote
# services, like the artifact cache, source cache and remote
# execution service.
#
class RemoteSpec:
    def __init__(
        self,
        remote_type: str,
        url: str,
        *,
        push: bool = False,
        server_cert: str = None,
        client_key: str = None,
        client_cert: str = None,
        instance_name: Optional[str] = None,
        spec_node: Optional[MappingNode] = None,
    ) -> None:

        #
        # Public members
        #

        # The remote type
        self.remote_type: str = remote_type

        # Whether we are allowed to push (for asset caches only)
        self.push: bool = push

        # The url of the remote, this may contain a port number
        self.url: str = url

        # The name of the grpc service to talk to at this remote url
        self.instance_name: Optional[str] = instance_name

        # The credentials
        self.server_cert_file: Optional[str] = server_cert
        self.client_key_file: Optional[str] = client_key
        self.client_cert_file: Optional[str] = client_cert

        #
        # Private members
        #

        # The provenance node for error reporting
        self._spec_node: Optional[MappingNode] = spec_node

        # The credentials loaded from disk, and whether they were loaded
        self._server_cert: Optional[bytes] = None
        self._client_key: Optional[bytes] = None
        self._client_cert: Optional[bytes] = None
        self._cred_files_loaded: bool = False

        # The grpc credentials object
        self._credentials: Optional[ChannelCredentials] = None

    #
    # Implement dunder methods to support hashing and
    # comparisons.
    #
    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other)

    def __hash__(self) -> int:
        return hash(
            (
                self.remote_type,
                self.push,
                self.url,
                self.instance_name,
                self.server_cert_file,
                self.client_key_file,
                self.client_cert_file,
            )
        )

    def __str__(self) -> str:
        string = self.url + "\n"
        string += "push: {} type: {} instance: {}\n".format(self.push, self.remote_type, self.instance_name)
        if self._spec_node:
            provenance = str(self._spec_node.get_provenance())
        else:
            provenance = "command line"
        string += "loaded from: {}".format(provenance)

        return string

    # server_cert()
    #
    @property
    def server_cert(self) -> Optional[bytes]:
        self._load_credential_files()
        return self._server_cert

    # client_key()
    #
    @property
    def client_key(self) -> Optional[bytes]:
        self._load_credential_files()
        return self._client_key

    # client_cert()
    #
    @property
    def client_cert(self) -> Optional[bytes]:
        self._load_credential_files()
        return self._client_cert

    # credentials()
    #
    @property
    def credentials(self) -> ChannelCredentials:
        if not self._credentials:
            self._credentials = grpc.ssl_channel_credentials(
                root_certificates=self.server_cert, private_key=self.client_key, certificate_chain=self.client_cert,
            )
        return self._credentials

    # open_channel()
    #
    # Opens a gRPC channel based on this spec.
    #
    def open_channel(self) -> Channel:
        url = urlparse(self.url)

        # Assert port number for RE endpoints
        #
        if self.remote_type == RemoteType.ENDPOINT and not url.port:
            message = (
                "Remote execution endpoints must specify the port number, for example: http://buildservice:50051."
            )
            if self._spec_node:
                message = "{}: {}".format(self._spec_node.get_provenance(), message)
            raise RemoteError(message)

        if url.scheme == "http":
            channel = grpc.insecure_channel("{}:{}".format(url.hostname, url.port or 80))
        elif url.scheme == "https":
            channel = grpc.secure_channel("{}:{}".format(url.hostname, url.port or 443), self.credentials)
        else:
            message = "Only 'http' and 'https' protocols are supported, but '{}' was supplied.".format(url.scheme)
            if self._spec_node:
                message = "{}: {}".format(self._spec_node.get_provenance(), message)
            raise RemoteError(message)

        return channel

    # new_from_node():
    #
    # Creates a RemoteSpec() from a YAML loaded node.
    #
    # Args:
    #    spec_node: The configuration node describing the spec.
    #    basedir: The base directory from which to find certificates.
    #    remote_execution: Whether this spec is used for remote execution (some keys are invalid)
    #
    # Returns:
    #    The described RemoteSpec instance.
    #
    # Raises:
    #    LoadError: If the node is malformed.
    #
    @classmethod
    def new_from_node(
        cls, spec_node: MappingNode, basedir: Optional[str] = None, *, remote_execution: bool = False
    ) -> "RemoteSpec":
        valid_keys = ["url", "server-cert", "client-key", "client-cert", "instance-name"]

        if remote_execution:
            remote_type = RemoteType.ENDPOINT
            push = False
        else:
            remote_type = cast(str, spec_node.get_enum("type", RemoteType, default=RemoteType.ALL))
            push = spec_node.get_bool("push", default=False)
            valid_keys += ["push", "type"]

        spec_node.validate_keys(valid_keys)

        # FIXME: This explicit error message should not be necessary, instead
        #        we should be able to inform Node.get_str() that an empty string
        #        is not acceptable, and have Node do the work of raising this error.
        #
        url = spec_node.get_str("url")
        if not url:
            provenance = spec_node.get_node("url").get_provenance()
            raise LoadError("{}: empty artifact cache URL".format(provenance), LoadErrorReason.INVALID_DATA)

        instance_name = spec_node.get_str("instance-name", default=None)

        def parse_cert(key):
            cert = spec_node.get_str(key, default=None)
            if cert:
                cert = os.path.expanduser(cert)

                if basedir:
                    cert = os.path.join(basedir, cert)

            return cert

        cert_keys = ("server-cert", "client-key", "client-cert")
        server_cert, client_key, client_cert = tuple(parse_cert(key) for key in cert_keys)

        if client_key and not client_cert:
            provenance = spec_node.get_node("client-key").get_provenance()
            raise LoadError(
                "{}: 'client-key' was specified without 'client-cert'".format(provenance), LoadErrorReason.INVALID_DATA
            )

        if client_cert and not client_key:
            provenance = spec_node.get_node("client-cert").get_provenance()
            raise LoadError(
                "{}: 'client-cert' was specified without 'client-key'".format(provenance), LoadErrorReason.INVALID_DATA
            )

        return cls(
            remote_type,
            url,
            push=push,
            server_cert=server_cert,
            client_key=client_key,
            client_cert=client_cert,
            instance_name=instance_name,
            spec_node=spec_node,
        )

    # _load_credential_files():
    #
    # A helper method to load the credentials files, ignoring any input
    # arguments that are None.
    #
    def _load_credential_files(self) -> None:
        def maybe_read_file(filename: Optional[str]) -> Optional[bytes]:
            if filename:
                try:
                    with open(filename, "rb") as f:
                        return f.read()
                except IOError as e:
                    message = "Failed to load credentials file: {}".format(filename)
                    if self._spec_node:
                        message = "{}: {}".format(self._spec_node.get_provenance(), message)
                    raise RemoteError(message, detail=str(e), reason="load-remote-creds-failed") from e
            return None

        if not self._cred_files_loaded:
            self._server_cert = maybe_read_file(self.server_cert_file)
            self._client_key = maybe_read_file(self.client_key_file)
            self._client_cert = maybe_read_file(self.client_cert_file)
            self._cred_files_loaded = True


# RemoteExecutionSpec():
#
# This data structure holds all of the details required to
# connect to a remote execution cluster, it is essentially
# comprised of 3 RemoteSpec objects which are used to
# communicate with various components of an RE build cluster.
#
class RemoteExecutionSpec:
    def __init__(self, exec_spec: RemoteSpec, storage_spec: RemoteSpec, action_spec: Optional[RemoteSpec]) -> None:
        self.exec_spec: RemoteSpec = exec_spec
        self.storage_spec: RemoteSpec = storage_spec
        self.action_spec: Optional[RemoteSpec] = action_spec

    # new_from_node():
    #
    # Creates a RemoteExecutionSpec() from a YAML loaded node.
    #
    # Args:
    #    node: The node to parse
    #    basedir: The base directory from which to find certificates.
    #
    # Returns:
    #    The described RemoteSpec instance.
    #
    # Raises:
    #    LoadError: If the node is malformed.
    #
    @classmethod
    def new_from_node(cls, node: MappingNode, basedir: Optional[str] = None) -> "RemoteExecutionSpec":
        node.validate_keys(["execution-service", "storage-service", "action-cache-service"])

        exec_node = node.get_mapping("execution-service")
        storage_node = node.get_mapping("storage-service")
        action_node = node.get_mapping("action-cache-service", default=None)

        exec_spec = RemoteSpec.new_from_node(exec_node, basedir, remote_execution=True)
        storage_spec = RemoteSpec.new_from_node(storage_node, basedir, remote_execution=True)

        action_spec: Optional[RemoteSpec]
        if action_node:
            action_spec = RemoteSpec.new_from_node(action_node, basedir, remote_execution=True)
        else:
            action_spec = None

        return cls(exec_spec, storage_spec, action_spec)
