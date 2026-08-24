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

    # --- resolved against a root given at call time ----------------------
    #: One manager's working directory under a run's log root: `<log_root>/tool`,
    #: `<log_root>/memory`, and so on. Every manager joined this itself, twice —
    #: once in its server and once in its context — so the same rule was written
    #: forty-odd times and the layout table did not know about any of it.
    LOG_MODULE = "log_module"
    #: Files a manager keeps beside its own directory, under the same log root.
    LOG_TASKS = "log_tasks"
    LOG_TASKS_ARCHIVE = "log_tasks_archive"
    LOG_TRACE_INDEX = "log_trace_index"
    #: The three directories a project root is made of. `ProjectSandbox` builds
    #: these for a root it is handed, which may be any directory, so it cannot ask
    #: for a session key — but the leaf names still belong to the table.
    PROJECT_WORKSPACE = "project_workspace"
    PROJECT_LOG = "project_log"
    PROJECT_EXTENSION = "project_extension"

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
    #: Where the bytes of an image the agent read are pinned, content-addressed, so
    #: the model's view of it cannot change when the source file does. Machine-level
    #: for the same reason as SPILL: a tool context carries neither owner nor session.
    ATTACHMENTS = "attachments"

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
    #: The three directories a session owns. ``workspace`` was the only one with a key
    #: of its own, so ``log`` and ``extension`` were reachable only by joining a
    #: ``PROJECT_*`` fragment onto a root the caller already had — two algorithms for one
    #: directory, and the table had no say in the second. A session's staged extension
    #: tree is where an evolution run writes; nothing is promoted to ``EXTENSION`` until
    #: it validates.
    SESSION_WORKSPACE = "session_workspace"
    SESSION_LOG = "session_log"
    SESSION_EXTENSION = "session_extension"
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
    SESSION_PLAN = "session_plan"
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
#: Keys resolved against a root the caller supplies, through
#: :meth:`PathManagerServer.under` rather than :meth:`get`. They are fragments — a
#: leaf name or a `{module}` slot — so they have no home of their own and the rule
#: that every declared path stays inside the two writable roots is enforced on the
#: root they are joined to, not on them.
RELATIVE: frozenset = frozenset({P.LOG_MODULE, P.LOG_TASKS, P.LOG_TASKS_ARCHIVE,
                                 P.LOG_TRACE_INDEX, P.PROJECT_WORKSPACE,
                                 P.PROJECT_LOG, P.PROJECT_EXTENSION})

LAYOUT: Dict[P, str] = {
    #: A manager's own working directory under whichever log root the run is bound
    #: to, and the three roots a project directory is made of. Both are resolved
    #: against a root supplied at call time — a session's log root moves when the
    #: session binds, and a project root may be any directory — so they are joined
    #: through :meth:`PathManagerServer.under`, not :meth:`get`.
    P.LOG_MODULE: "{module}",
    P.LOG_TASKS: "tasks.json",
    P.LOG_TASKS_ARCHIVE: "tasks_archive.json",
    P.LOG_TRACE_INDEX: "index.json",
    P.PROJECT_WORKSPACE: "workspace",
    P.PROJECT_LOG: "log",
    P.PROJECT_EXTENSION: "extension",

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
    P.ATTACHMENTS: "output/.runtime/attachments",

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
    P.SESSION_LOG: "output/{owner}/sessions/{session_id}/log",
    P.SESSION_EXTENSION: "output/{owner}/sessions/{session_id}/extension",
    P.SESSION_MANIFEST: "output/{owner}/sessions/{session_id}/session.json",
    P.SESSION_TRACE: "output/{owner}/sessions/{session_id}/log/trace",
    P.SESSION_FLOWS: "output/{owner}/sessions/{session_id}/flows",
    P.SESSION_RUNS: "output/{owner}/sessions/{session_id}/runs",
    P.CONVERSATIONS: "output/{owner}/sessions/{session_id}/conversations",
    P.CONVERSATION_EVENTS: "output/{owner}/sessions/{session_id}/conversations/{conversation_id}.jsonl",
    P.CONVERSATION_META: "output/{owner}/sessions/{session_id}/conversations/{conversation_id}.json",
    P.SESSION_GOALS: "output/{owner}/sessions/{session_id}/goals.json",
    #: The run's plan, as a document. A file rather than a field so the person
    #: watching can read it, the agent can revise it with the tools it already has,
    #: and it outlives the process — a plan held only in `PlanState` was gone the
    #: moment the run ended, including the one a person had just approved.
    #:
    #: Inside the workspace, not beside it. Every other session-level file here is
    #: framework state the agent never touches; this one the agent writes, and
    #: `workspace_write` is exactly the permission it holds. A sibling of `workspace/`
    #: refused `write_file_tool` — "outside the writable roots" — which would have made
    #: `auto` a mode that asks for a document the agent is not allowed to create.
    P.SESSION_PLAN: "output/{owner}/sessions/{session_id}/workspace/plan.md",
    P.SESSION_IDE_USER_DATA: "output/{owner}/sessions/{session_id}/ide/user-data",
    P.SESSION_NOTEBOOKS: "output/{owner}/sessions/{session_id}/workspace/notebooks",
    P.RUN: "output/{owner}/runs/{run_id}",
}

__all__ = ["P", "LAYOUT"]
