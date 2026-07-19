import os
from typing import Union

from agentevolver.paths import data_path, extension_root, package_root, resource_path


def get_extension_root() -> str:
    """Writable directory containing self-evolved extension components."""
    return str(extension_root())


def get_package_root() -> str:
    """The installed package directory — for shipped, read-only resources."""
    return str(package_root())


def assemble_workspace_path(path: str) -> str:
    """Resolve a user-data path relative to the AgentEvolver home directory.

    Args:
        path: Path string (relative or absolute).

    Returns:
        Absolute path string. Absolute inputs are returned as-is.
    """
    return data_path(path)

def assemble_resource_path(path: str) -> str:
    """Resolve a shipped RESOURCE (e.g. a default config) that the user may override.

    Searches home → repo → package, so a config works both in a source checkout and
    when the package is pip-installed.
    """
    return resource_path(path)
