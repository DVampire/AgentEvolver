"""Single source of truth for the on-disk layout.

    from agentevolver.paths import P, path_manager

    path_manager.bind_session(owner, session_id)   # once, when the run's session opens
    path_manager.get(P.SESSION_WORKSPACE)          # anywhere after that, no arguments

Storage roots: ``output/`` for generated, machine- and user-specific state, and
``extension/`` for shared components, and ``memory/`` for durable Markdown notes.

Session-scoped keys resolve against the bound run, so a caller does not have to have been
handed a path to know where it may write. Passing ``owner`` / ``session_id`` explicitly
asks about a *particular* session instead — which the host side of a containerised run
needs, since it must resolve the real directory it is about to mount while the agent
inside sees only the mount point.
"""

from .server import PathManagerServer, path_manager
from .types import FILES, RELATIVE, LAYOUT, P

__all__ = ["path_manager", "PathManagerServer", "P", "LAYOUT", "RELATIVE", "FILES"]
