import os
from buildstream import _yaml


# Shared function to configure the project.conf inline
#
def configure_project(path, config):
    config["name"] = "test"
    config["min-version"] = "2.0"
    config["element-path"] = "elements"
    config["plugins"] = [
        {
            "origin": "pip",
            "package-name": "sample-plugins",
            "sources": ["git"],
        }
    ]

    _yaml.roundtrip_dump(config, os.path.join(path, "project.conf"))
