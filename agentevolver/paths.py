"""Path resolution for AgentEvolver — the single place that knows where things live.

Two distinct roots, kept separate so the package works both from the repo and when
pip-installed into an arbitrary environment:

- **package root** — the installed ``agentevolver/`` directory. Shipped, read-only
  resources live here (default prompts, visual assets, bundled skills, default configs
  once bundled). Located via ``__file__`` so it is correct wherever the package installs.

- **home dir** — a user-writable base for run data: ``work_dir``, ``run_dir``,
  generated capabilities under ``extension/``, logs, checkpoints. Chosen by the
  ``AGENTEVOLVER_HOME`` environment variable, else the current working directory. This is
  never inside site-packages, so an installed package never writes into itself.

Resources the user may override (configs) are looked up in home → repo → package order.
"""
import os
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent      # .../agentevolver
_REPO_ROOT = _PACKAGE_ROOT.parent                    # repo root — only meaningful in a source checkout


def package_root() -> Path:
    """The installed package directory — for shipped, read-only resources."""
    return _PACKAGE_ROOT


def home_dir() -> Path:
    """User-writable base for run data. ``AGENTEVOLVER_HOME`` if set, else the cwd."""
    env = os.environ.get("AGENTEVOLVER_HOME")
    if env:
        p = Path(env).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()
    return Path.cwd()


def data_path(rel: str = "") -> str:
    """Resolve a user-DATA path. Absolute stays absolute; relative resolves under home."""
    if not rel:
        return str(home_dir())
    if os.path.isabs(rel):
        return os.path.abspath(rel)
    return str(home_dir() / rel)


def resource_path(rel: str) -> str:
    """Resolve a shipped RESOURCE (e.g. a default config) the user may override.

    Search order: home_dir (user override) → repo root (editable/source checkout) →
    package (installed wheel). Falls back to the package path so callers always get a
    stable location string even when the file is absent.
    """
    if os.path.isabs(rel):
        return os.path.abspath(rel)
    for base in (home_dir(), _REPO_ROOT, _PACKAGE_ROOT):
        candidate = base / rel
        if candidate.exists():
            return str(candidate)
    return str(_PACKAGE_ROOT / rel)
