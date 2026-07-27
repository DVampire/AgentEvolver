"""Layout keys and the table they index."""

from __future__ import annotations

from enum import Enum
from typing import Dict


class P(str, Enum):
    """Keys into :data:`LAYOUT`.

    An enum rather than bare strings so a typo is a static error with editor
    completion, instead of a path containing a literal ``{owner}`` discovered
    much later.
    """

    # --- the two buckets -------------------------------------------------
    OUTPUT = "output"
    EXTENSION = "extension"

    # --- machine-level runtime state (belongs to the host, not a user) ---
    RUNTIME = "runtime"
    PORTS = "ports"
    LEDGER = "ledger"
    DEPLOY = "deploy"
    STAGING = "staging"
    #: Where output lands before anything binds a session — the gateway's own
    #: startup logs, or a direct run that never opened one.
    UNBOUND = "unbound"

    # --- per owner (durable, survives every session) ---------------------
    OWNER = "owner"
    OWNER_STATE = "owner_state"
    OWNER_FILES = "owner_files"
    OWNER_FLOWS = "owner_flows"
    OWNER_IDE = "owner_ide"
    IDE_EXTENSIONS = "ide_extensions"
    IDE_USER_DATA = "ide_user_data"
    IDE_HOME = "ide_home"

    # --- per session / per run (disposable) ------------------------------
    SESSIONS = "sessions"
    SESSION = "session"
    SESSION_WORKSPACE = "session_workspace"
    SESSION_MANIFEST = "session_manifest"
    RUN = "run"


#: The complete tree, relative to the project directory. Two roots only:
#: ``output/`` for generated, machine- and user-specific state, and
#: ``extension/`` for shared, durable components. Everything the framework
#: writes is declared here — this table *is* the disk contract.
LAYOUT: Dict[P, str] = {
    P.OUTPUT: "output",
    P.EXTENSION: "extension",

    P.RUNTIME: "output/.runtime",
    P.PORTS: "output/.runtime/ports.json",
    P.LEDGER: "output/.runtime/sandbox_ledger.json",
    P.DEPLOY: "output/.runtime/deploy",
    P.STAGING: "output/.runtime/staging/{project_key}",
    P.UNBOUND: "output/.runtime/unbound",

    P.OWNER: "output/{owner}",
    P.OWNER_STATE: "output/{owner}/state",
    P.OWNER_FILES: "output/{owner}/state/files",
    P.OWNER_FLOWS: "output/{owner}/state/flows",
    P.OWNER_IDE: "output/{owner}/state/ide",
    P.IDE_EXTENSIONS: "output/{owner}/state/ide/extensions",
    P.IDE_USER_DATA: "output/{owner}/state/ide/user-data",
    P.IDE_HOME: "output/{owner}/state/ide/home",

    P.SESSIONS: "output/{owner}/sessions",
    P.SESSION: "output/{owner}/sessions/{session_id}",
    P.SESSION_WORKSPACE: "output/{owner}/sessions/{session_id}/workspace",
    P.SESSION_MANIFEST: "output/{owner}/sessions/{session_id}/session.json",
    P.RUN: "output/{owner}/runs/{run_id}",
}

__all__ = ["P", "LAYOUT"]
