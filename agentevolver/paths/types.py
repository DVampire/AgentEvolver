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
    #: One module's shared components — ``extension/skill``, ``extension/tool``,
    #: ``extension/canvas``, … Parameterised rather than one key per module,
    #: because the extension manager promotes any module type through the same
    #: path.
    EXTENSION_MODULE = "extension_module"

    # --- machine-level runtime state (belongs to the host, not a user) ---
    RUNTIME = "runtime"
    PORTS = "ports"
    LEDGER = "ledger"
    SSH_HOSTS = "ssh_hosts"
    DEPLOY = "deploy"
    CHECKPOINTS = "checkpoints"
    STAGING = "staging"
    #: Where an oversized tool result is parked so the agent keeps a way back to
    #: it. Machine-level rather than per session: the store hashes the session
    #: into a subdirectory itself, so a caller holding only a tool context — which
    #: carries no owner or session id — can still resolve the root.
    SPILL = "spill"

    # --- per owner (durable, survives every session) ---------------------
    OWNER = "owner"
    OWNER_STATE = "owner_state"
    OWNER_FILES = "owner_files"
    OWNER_IDE = "owner_ide"
    IDE_EXTENSIONS = "ide_extensions"
    IDE_HOME = "ide_home"
    #: $HOME for the Science workstation: pip installs, wandb logins and
    #: Jupyter settings, kept per owner so they outlive a reaped container.
    SCIENCE_HOME = "science_home"

    # --- per session / per run (disposable) ------------------------------
    SESSIONS = "sessions"
    SESSION = "session"
    SESSION_WORKSPACE = "session_workspace"
    SESSION_MANIFEST = "session_manifest"
    #: Where trace writes one JSONL file per run, plus its `index.json`. A session
    #: directory holds several: the run the session was opened for and every
    #: sub-agent run it spawned, each under its own trace session id. This is the
    #: durable record cross-session retrieval reads.
    SESSION_TRACE = "session_trace"
    #: Canvas drafts belong to the session that drew them; a finished flow is
    #: promoted to the shared library under ``extension/canvas``.
    SESSION_FLOWS = "session_flows"
    #: One append-only index per flow: every run it has had, newest last.
    SESSION_RUNS = "session_runs"
    #: One conversation's transcript, append-only. The in-memory buffer is
    #: bounded and dies with the process; this is what lets a restored project
    #: reopen with its conversations instead of an empty transcript.
    CONVERSATION_EVENTS = "conversation_events"
    #: A conversation's identity: title, which view it belongs to, timestamps.
    CONVERSATION_META = "conversation_meta"
    #: All conversations of one project.
    CONVERSATIONS = "conversations"
    #: The session's goals — what a human asked for and where it stands. Beside
    #: the session manifest rather than in the workspace: a goal outlives the
    #: process and must not be reachable by the file tools the agent uses on its
    #: own work, or the agent could rewrite the objective it is measured against.
    SESSION_GOALS = "session_goals"
    #: Editor state — open tabs, layout. Per session, unlike the extensions and
    #: agent logins beside it, which are worth sharing across all of them.
    SESSION_IDE_USER_DATA = "session_ide_user_data"
    #: Notebooks the Science view writes. Under the workspace, not beside it, so
    #: bash and code_interpreter — which start in the workspace — can open the
    #: same files the notebook wrote, and the files pane lists them.
    SESSION_NOTEBOOKS = "session_notebooks"
    RUN = "run"


#: The complete tree, relative to the project directory. Two roots only:
#: ``output/`` for generated, machine- and user-specific state, and
#: ``extension/`` for shared, durable components. Everything the framework
#: writes is declared here — this table *is* the disk contract.
LAYOUT: Dict[P, str] = {
    P.OUTPUT: "output",
    P.EXTENSION: "extension",
    P.EXTENSION_MODULE: "extension/{module}",

    P.RUNTIME: "output/.runtime",
    P.PORTS: "output/.runtime/ports.json",
    P.LEDGER: "output/.runtime/sandbox_ledger.json",
    P.SSH_HOSTS: "output/.runtime/ssh_hosts.json",
    P.DEPLOY: "output/.runtime/deploy",
    P.CHECKPOINTS: "output/.runtime/checkpoints",
    P.STAGING: "output/.runtime/staging/{project_key}",
    P.SPILL: "output/.runtime/spill",

    P.OWNER: "output/{owner}",
    P.OWNER_STATE: "output/{owner}/state",
    P.OWNER_FILES: "output/{owner}/state/files",
    P.OWNER_IDE: "output/{owner}/state/ide",
    P.IDE_EXTENSIONS: "output/{owner}/state/ide/extensions",
    P.IDE_HOME: "output/{owner}/state/ide/home",
    P.SCIENCE_HOME: "output/{owner}/state/science/home",

    P.SESSIONS: "output/{owner}/sessions",
    P.SESSION: "output/{owner}/sessions/{session_id}",
    P.SESSION_WORKSPACE: "output/{owner}/sessions/{session_id}/workspace",
    P.SESSION_MANIFEST: "output/{owner}/sessions/{session_id}/session.json",
    P.SESSION_TRACE: "output/{owner}/sessions/{session_id}/log/trace",
    P.SESSION_FLOWS: "output/{owner}/sessions/{session_id}/flows",
    P.SESSION_RUNS: "output/{owner}/sessions/{session_id}/runs",
    P.CONVERSATIONS: "output/{owner}/sessions/{session_id}/conversations",
    P.CONVERSATION_EVENTS: "output/{owner}/sessions/{session_id}/conversations/{conversation_id}.jsonl",
    P.CONVERSATION_META: "output/{owner}/sessions/{session_id}/conversations/{conversation_id}.json",
    P.SESSION_GOALS: "output/{owner}/sessions/{session_id}/goals.json",
    P.SESSION_IDE_USER_DATA: "output/{owner}/sessions/{session_id}/ide/user-data",
    P.SESSION_NOTEBOOKS: "output/{owner}/sessions/{session_id}/workspace/notebooks",
    P.RUN: "output/{owner}/runs/{run_id}",
}

__all__ = ["P", "LAYOUT"]
