"""Session-owned Bash container routes; never change process-global environment vars."""
from pathlib import Path

_routes: dict[tuple[str, str], tuple[str, str]] = {}


def _key(workspace, owner):
    if owner is None:
        from agentevolver.runtime.invocation import current_owner
        owner = current_owner()
    return str(Path(workspace).resolve()), owner


def bind_container(workspace: Path, container: str, *, owner: str | None = None) -> None:
    key = _key(workspace, owner)
    current = _routes.get(key)
    if current and current[0] != container:
        raise ValueError(f"Workspace already has a Bash container: {key}")
    _routes[key] = (container, key[0])


def container_for(workspace: str, *, owner: str | None = None) -> tuple[str, str] | None:
    if not workspace:
        return None
    key = _key(workspace, owner)
    return _routes.get(key) or _routes.get((key[0], ""))


def unbind_container(workspace: Path, container: str, *, owner: str | None = None) -> None:
    key = _key(workspace, owner)
    if _routes.get(key, (None,))[0] == container:
        _routes.pop(key, None)
