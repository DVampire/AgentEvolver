"""Helpers for attaching every execution context to a project sandbox."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.sandbox.project import ProjectSandbox
from agentevolver.utils.file_utils import atomic_write_text

#: Compatibility name for callers that display the filename. The path itself is
#: resolved from ``P.PROJECT_MANIFEST`` below.
SESSION_MANIFEST = path_manager.under(".", P.PROJECT_MANIFEST).name


def configured_session_owner(config_obj: Any = None) -> str:
    """Namespace direct-run sessions by explicit owner, then by config tag."""
    selected = config_obj or config
    getter = getattr(selected, "get", None)
    if callable(getter):
        return str(getter("output_owner") or getter("tag") or "local")
    return str(
        getattr(selected, "output_owner", None)
        or getattr(selected, "tag", None)
        or "local"
    )


def write_session_manifest(
    sandbox: ProjectSandbox,
    *,
    session_id: str,
    owner: str | None = None,
    name: str = "interactive",
    created_at: str | None = None,
    source_workspace: str | None = None,
) -> None:
    """Record a session's identity next to its files.

    Lives here rather than in the gateway so *whoever* creates a session writes
    the same manifest. When only the gateway did it, a locally-started run
    produced a directory the gateway could neither list nor restore — the same
    work, silently second-class depending on how it was launched.

    Deliberately small: identity and roots, no conversation history. The event
    log is a bounded in-memory ring, so a restored transcript would be a partial
    one pretending to be complete.
    """
    owner = owner or configured_session_owner()
    path = path_manager.under(sandbox.project_root, P.PROJECT_MANIFEST)
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "session_id": session_id,
        "name": name,
        "owner": owner,
        "created_at": created_at or now,
        # Rewritten every time work happens, so the project list can lead with
        # what was touched last. ``created_at`` orders by birth, which puts a
        # project someone has been living in all week below one opened once and
        # abandoned.
        "updated_at": now,
        "source_workspace": source_workspace,
    }
    try:
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))
    except OSError as exc:  # noqa: BLE001 — never fail a run over bookkeeping
        logger.warning(f"| ⚠️ Could not write session manifest for {session_id}: {exc}")


def ensure_session_sandbox(
    ctx: Any,
    *,
    owner: str | None = None,
    shared_extension_root: str | Path | None = None,
) -> ProjectSandbox | None:
    """Bind the path manager to ``ctx``'s session, and return that session's sandbox.

    The session lands exactly where the gateway puts one — ``output/<owner>/sessions/<id>``
    from the layout table — so a task started from a local config and the same task
    started from the browser use the same directory.

    Binding rather than describing. The roots used to be written into ``ctx.extra`` as six
    strings and carried down through every manager, agent, hook and tool, and both halves
    of that went wrong: ``extension_root`` in the dict was the session's writable staging
    tree while ``config.extension_root`` was the shared library, so one name meant two
    opposite directories; and anything holding the dict could rewrite it, which left the
    layout table advisory. Now the table answers directly, wherever the question is asked.

    ``None`` when this session is already bound — the agent manager calls this on every
    invocation so a directly-constructed context still gets a sandbox, and re-binding the
    same session on a nested call would rewrite the manifest for no reason.
    """
    owner = owner or configured_session_owner()
    session_id = str(getattr(ctx, "id", "") or "direct")
    if path_manager.session == (owner, session_id):
        return None

    root = path_manager.get(P.SESSION, owner=owner, session_id=session_id)
    sandbox = ProjectSandbox.create(
        root,
        shared_extension_root=shared_extension_root,
    )
    write_session_manifest(
        sandbox,
        session_id=session_id,
        owner=owner,
        name=str(getattr(ctx, "name", "") or "interactive"),
    )
    path_manager.bind_session(owner, session_id)
    return sandbox


def bind_session_roots(config: Any, sandbox: ProjectSandbox) -> None:
    """Move config-derived manager state from tag templates into one session."""
    old_workspace = Path(str(config.workspace_root)).resolve()
    old_log = Path(str(config.log_root)).resolve()

    def rebase(value: Any) -> None:
        if isinstance(value, dict):
            base_dir = value.get("base_dir")
            if isinstance(base_dir, str):
                candidate = Path(base_dir).expanduser().resolve()
                for source, target in ((old_workspace, sandbox.workspace_root), (old_log, sandbox.log_root)):
                    try:
                        value["base_dir"] = str(target / candidate.relative_to(source))
                        break
                    except ValueError:
                        continue
            for child in value.values():
                rebase(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                rebase(child)

    for value in config.values():
        rebase(value)
    log_name = Path(str(getattr(config, "log_path", "agent.log"))).name
    config.project_root = str(sandbox.project_root)
    config.workspace_root = str(sandbox.workspace_root)
    config.log_root = str(sandbox.log_root)
    config.log_path = str(sandbox.log_root / log_name)


def stage_input_files(ctx: Any, input: Dict[str, Any]) -> Dict[str, Any]:
    """Copy external task attachments into the session tree before execution.

    Direct Python entry points commonly receive task documents from a checkout, and a
    sandboxed agent may not read those source paths — ``check_session_path`` allows only
    the session's own roots — so their file inputs are copied in and the agent receives
    only the staged paths.

    Staged under ``log_root/inputs``, not into the workspace. The workspace holds the
    agent's *deliverable*; an attachment is an input to the run, and putting one there
    left an `inputs/` directory sitting in the middle of the agent's output — and, where a
    run's workspace is packaged up, shipped it with the work. ``log_root`` is on the
    readable side of ``check_session_path``, so the agent can still open what it is given.

    Gateway uploads already live in the workspace and are left untouched.
    """
    prepared = dict(input)
    files = prepared.get("files")
    if not isinstance(files, list) or not config.workspace_root:
        return prepared

    workspace = Path(config.workspace_root).resolve()
    inputs_dir = path_manager.under(Path(config.log_root).resolve(), P.LOG_INPUTS)
    staged: list[str] = []
    for index, value in enumerate(files):
        source = Path(str(value)).expanduser().resolve()
        try:
            source.relative_to(workspace)
            staged.append(str(source))
            continue
        except ValueError:
            pass
        if not source.is_file():
            # Preserve a missing path so the receiving agent can report it normally.
            staged.append(str(source))
            continue
        inputs_dir.mkdir(parents=True, exist_ok=True)
        destination = inputs_dir / f"{index:03d}_{source.name}"
        shutil.copy2(source, destination)
        staged.append(str(destination))
    prepared["files"] = staged
    return prepared


def read_session_manifest(project_root: str | Path) -> Optional[Dict[str, Any]]:
    """Read one project's manifest back, or ``None`` when there is not one to read.

    `write_session_manifest` says it exists so a locally-started run is not second-class,
    and so a listing can lead with what was touched last. Nothing read it. The gateway
    orders its list from `self._sessions`, which is memory — so the answer to "what have I
    been working on" was whatever this process happened to have seen, and a restart
    emptied it while the manifests sat on disk beside every project.

    `None` covers absent, unreadable and malformed alike, and each is logged: a directory
    with no manifest is an ordinary older project, not a fault, but a manifest that will
    not parse is one, and a caller scanning a tree cannot tell them apart from the return
    value.
    """
    path = path_manager.under(project_root, P.PROJECT_MANIFEST)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning(f"| ⚠️ Could not read session manifest at {path}: {error}")
        return None
    if not isinstance(payload, dict) or not payload.get("session_id"):
        logger.warning(f"| ⚠️ Session manifest at {path} has no session_id; ignoring it")
        return None
    return payload


def list_session_manifests(sessions_root: str | Path) -> List[Dict[str, Any]]:
    """Every project under a root, most recently touched first.

    The order the manifest was written for. `updated_at` is rewritten whenever work
    happens, while `created_at` would put a project someone has lived in all week below
    one opened once and abandoned.
    """
    root = Path(sessions_root)
    if not root.is_dir():
        return []
    found = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        manifest = read_session_manifest(child)
        if manifest is not None:
            found.append(manifest)
    return sorted(found,
                  key=lambda m: str(m.get("updated_at") or m.get("created_at") or ""),
                  reverse=True)
