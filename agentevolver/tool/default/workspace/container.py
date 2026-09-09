"""Session-owned Bash container routes; never change process-global environment vars."""
from pathlib import Path

_routes: dict[str, tuple[str, str]] = {}


def bind_container(workspace: Path, container: str) -> None:
    key = str(workspace.resolve())
    current = _routes.get(key)
    if current and current[0] != container:
        raise ValueError(f"Workspace already has a Bash container: {key}")
    _routes[key] = (container, key)


def container_for(workspace: str) -> tuple[str, str] | None:
    return _routes.get(str(Path(workspace).resolve())) if workspace else None


def unbind_container(workspace: Path, container: str) -> None:
    key = str(workspace.resolve())
    if _routes.get(key, (None,))[0] == container:
        _routes.pop(key, None)
