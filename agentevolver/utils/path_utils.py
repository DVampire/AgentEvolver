import os
from typing import Union

from agentevolver.paths import home_dir, package_root, data_path, resource_path


def get_project_root() -> str:
    """The user's project root — where run data (work_dir, extension, logs) lives.

    This is the ``AGENTEVOLVER_HOME`` directory, or the current working directory. It is
    NOT the installed package location, so an installed package never writes into itself.
    Kept as the base for :func:`assemble_project_path` (all its callers are user data).
    """
    return str(home_dir())


def get_package_root() -> str:
    """The installed package directory — for shipped, read-only resources."""
    return str(package_root())


def assemble_project_path(path: str) -> str:
    """Resolve a user-DATA path relative to the project root (home).

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
