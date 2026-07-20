"""Canonical path resolution for AgentEvolver's writable and bundled resources."""

import os
from pathlib import Path


_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PACKAGE_ROOT.parent


def package_root() -> Path:
    """The installed package directory, containing shipped read-only resources."""
    return _PACKAGE_ROOT


def home_dir() -> Path:
    """Stable user-writable base for user-level config and state.

    Defaults to ``.agentevolver`` in the current project directory (like ``.git``),
    so user-level state lives inside the project rather than the OS home directory.
    ``AGENTEVOLVER_HOME`` overrides it (e.g. to share one home across projects).
    """
    override = os.environ.get("AGENTEVOLVER_HOME")
    root = Path(override).expanduser() if override else Path.cwd() / ".agentevolver"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def data_path(rel: str = "") -> str:
    """Resolve an absolute path or a user-data path relative to ``home_dir``."""
    if not rel:
        return str(home_dir())
    path = Path(rel).expanduser()
    return str(path.resolve() if path.is_absolute() else (home_dir() / path).resolve())


def project_path(rel: str = "") -> str:
    """Resolve a runtime path relative to the caller's current project directory.

    This is deliberately separate from :func:`data_path`: generated outputs and
    workspaces belong to the project being run, whereas ``data_path`` is reserved
    for user-level AgentEvolver state under ``home_dir`` (``.agentevolver``).
    """
    path = Path(rel).expanduser()
    return str(path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve())


def extension_root() -> Path:
    """Shared project extension repository; sessions stage changes elsewhere."""
    root = Path(os.environ.get("AGENTEVOLVER_EXTENSION_ROOT", "extension")).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resource_path(rel: str) -> str:
    """Find an overrideable shipped resource: home → source tree → package."""
    path = Path(rel).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    for base in (home_dir(), _REPO_ROOT, _PACKAGE_ROOT):
        candidate = base / path
        if candidate.exists():
            return str(candidate)
    return str((_PACKAGE_ROOT / path).resolve())


def get_extension_root() -> str:
    """Writable directory containing self-evolved extension components."""
    return str(extension_root())


def get_package_root() -> str:
    """The installed package directory — for shipped, read-only resources."""
    return str(package_root())


def assemble_workspace_path(path: str) -> str:
    """Resolve a workspace/runtime path relative to the current project directory.

    Args:
        path: Path string (relative or absolute).

    Returns:
        Absolute path string. Absolute inputs are returned as-is.
    """
    return project_path(path)

def assemble_resource_path(path: str) -> str:
    """Resolve a shipped RESOURCE (e.g. a default config) that the user may override.

    Searches home → repo → package, so a config works both in a source checkout and
    when the package is pip-installed.
    """
    return resource_path(path)
