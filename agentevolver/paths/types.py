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
    LOG_INPUTS = "log_inputs"
    LOG_MODEL_REQUEST = "log_model_request"
    LOG_MESSAGE_SNAPSHOT = "log_message_snapshot"
    LOG_COMMAND_CHECKPOINTS = "log_command_checkpoints"
    LOG_COMMAND_CHECKPOINT = "log_command_checkpoint"
    LOG_GATEWAY_TASKS = "log_gateway_tasks"
    LOG_TASK_VIEW = "log_task_view"
    LOG_BENCHMARK = "log_benchmark"
    LOG_BENCHMARK_RESULTS = "log_benchmark_results"
    LOG_BENCHMARK_RESULT = "log_benchmark_result"
    TRACE_EVENT_LOG = "trace_event_log"
    TRACE_SQLITE = "trace_sqlite"
    TRACE_INTEGRITY = "trace_integrity"
    TRACE_PROJECTIONS = "trace_projections"
    TRACE_PROJECTION_WATERMARK = "trace_projection_watermark"
    #: The three directories a project root is made of. `ProjectSandbox` builds
    #: these for a root it is handed, which may be any directory, so it cannot ask
    #: for a session key — but the leaf names still belong to the table.
    PROJECT_WORKSPACE = "project_workspace"
    PROJECT_LOG = "project_log"
    PROJECT_EXTENSION = "project_extension"
    PROJECT_MANIFEST = "project_manifest"
    PROJECT_RESULT = "project_result"
    PROJECT_PATCH = "project_patch"
    PROJECT_SUBMISSION = "project_submission"
    PROJECT_SUBMISSION_VIEW = "project_submission_view"
    PROJECT_EVAL_BRIDGE = "project_eval_bridge"
    PROJECT_RESULT_RUN = "project_result_run"

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
    CHECKPOINT = "checkpoint"
    STAGING_MANIFEST = "staging_manifest"
    #: Where an oversized tool result is parked so the agent keeps a way back to
    #: it. Machine-level rather than per session: the store hashes the session
    #: into a subdirectory itself, so a caller holding only a tool context — which
    #: carries no owner or session id — can still resolve the root.
    SPILL_SESSION = "spill_session"
    #: Where the bytes of an image the agent read are pinned, content-addressed, so
    #: the model's view of it cannot change when the source file does. Machine-level
    #: for the same reason as SPILL: a tool context carries neither owner nor session.
    ATTACHMENTS = "attachments"

    # --- per owner (durable, survives every session) ---------------------
    OWNER = "owner"
    OWNER_STATE = "owner_state"
    OWNER_FILES = "owner_files"
    IDE_EXTENSIONS = "ide_extensions"
    IDE_HOME = "ide_home"

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
    #: Where trace writes one JSONL file per run, plus its `index.json`. A session
    #: directory holds several: the run the session was opened for and every
    #: sub-agent run it spawned, each under its own trace session id. This is the
    #: durable record cross-session retrieval reads.
    SESSION_TRACE = "session_trace"
    #: One `.txt` per bash call, holding that command's complete output verbatim.
    #: The tool result the model reads is still bounded (full when small, an excerpt
    #: plus locator when large), but the whole transcript is archived here so nothing
    #: a command printed is ever lost — the same guarantee for foreground and for
    #: background jobs, whose in-memory ring buffer would otherwise drop the head.
    SESSION_BASH = "session_bash"
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
    SESSION_LEGACY_EVENTS = "session_legacy_events"
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


#: The complete tree, relative to the project directory. Two roots only:
#: ``output/`` for generated, machine- and user-specific state, and
#: ``extension/`` for shared, durable components. Everything the framework
#: writes is declared here — this table *is* the disk contract.
#: Keys resolved against a root the caller supplies, through
#: :meth:`PathManagerServer.under` rather than :meth:`get`. They are fragments — a
#: leaf name or a `{module}` slot — so they have no home of their own and the rule
#: that every declared path stays inside the two writable roots is enforced on the
#: root they are joined to, not on them.
RELATIVE: frozenset = frozenset({
    P.LOG_MODULE, P.LOG_TASKS, P.LOG_TASKS_ARCHIVE, P.LOG_TRACE_INDEX,
    P.LOG_INPUTS, P.LOG_MODEL_REQUEST, P.LOG_MESSAGE_SNAPSHOT,
    P.LOG_COMMAND_CHECKPOINTS, P.LOG_COMMAND_CHECKPOINT, P.LOG_GATEWAY_TASKS,
    P.LOG_TASK_VIEW, P.LOG_BENCHMARK, P.LOG_BENCHMARK_RESULTS,
    P.LOG_BENCHMARK_RESULT, P.TRACE_EVENT_LOG, P.TRACE_SQLITE, P.TRACE_INTEGRITY,
    P.TRACE_PROJECTIONS, P.TRACE_PROJECTION_WATERMARK,
    P.PROJECT_WORKSPACE, P.PROJECT_LOG,
    P.PROJECT_EXTENSION, P.PROJECT_MANIFEST, P.PROJECT_RESULT,
    P.PROJECT_PATCH, P.PROJECT_SUBMISSION, P.PROJECT_SUBMISSION_VIEW,
    P.PROJECT_EVAL_BRIDGE,
    P.PROJECT_RESULT_RUN,
})

#: Layout entries that name files rather than directories. Creation must use this
#: declaration, not a suffix heuristic: a directory can contain a dot and a file can
#: intentionally have no suffix.
FILES: frozenset = frozenset({
    P.LOG_TASKS, P.LOG_TASKS_ARCHIVE, P.LOG_TRACE_INDEX,
    P.LOG_MODEL_REQUEST, P.LOG_MESSAGE_SNAPSHOT, P.LOG_COMMAND_CHECKPOINT,
    P.LOG_TASK_VIEW, P.LOG_BENCHMARK_RESULT,
    P.TRACE_EVENT_LOG, P.TRACE_SQLITE, P.TRACE_INTEGRITY,
    P.TRACE_PROJECTION_WATERMARK,
    P.PROJECT_MANIFEST, P.PROJECT_RESULT, P.PROJECT_PATCH, P.PROJECT_SUBMISSION,
    P.PORTS, P.LEDGER, P.SSH_HOSTS, P.CHECKPOINT, P.STAGING_MANIFEST,
    P.CONVERSATION_EVENTS, P.CONVERSATION_META, P.SESSION_LEGACY_EVENTS,
    P.SESSION_GOALS, P.SESSION_PLAN,
})

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
    P.LOG_INPUTS: "inputs",
    P.LOG_MODEL_REQUEST: "model_requests/{agent_name}/{filename}",
    P.LOG_MESSAGE_SNAPSHOT: "messages/{agent_name}/{filename}",
    P.LOG_COMMAND_CHECKPOINTS: "command/checkpoints",
    P.LOG_COMMAND_CHECKPOINT: "command/checkpoints/{filename}",
    P.LOG_GATEWAY_TASKS: "gateway/tasks",
    P.LOG_TASK_VIEW: "{filename}",
    P.LOG_BENCHMARK: "benchmark/{benchmark}",
    P.LOG_BENCHMARK_RESULTS: "results/{benchmark}",
    P.LOG_BENCHMARK_RESULT: "results/{benchmark}/{filename}",
    P.TRACE_EVENT_LOG: "{session_id}.jsonl",
    P.TRACE_SQLITE: "trace.sqlite3",
    P.TRACE_INTEGRITY: "integrity/{digest}.json",
    P.TRACE_PROJECTIONS: "projections",
    P.TRACE_PROJECTION_WATERMARK: "projections/{projection}/{filename}",
    P.PROJECT_WORKSPACE: "workspace",
    P.PROJECT_LOG: "log",
    P.PROJECT_EXTENSION: "extension",
    P.PROJECT_MANIFEST: "session.json",
    P.PROJECT_RESULT: "result.json",
    P.PROJECT_PATCH: "agent.patch",
    P.PROJECT_SUBMISSION: "submission.tar.gz",
    P.PROJECT_SUBMISSION_VIEW: "submission",
    P.PROJECT_EVAL_BRIDGE: "eval_bridge",
    P.PROJECT_RESULT_RUN: "results/{run_id}",

    P.OUTPUT: "output",
    P.EXTENSION: "extension",
    P.EXTENSION_MODULE: "extension/{module}",

    P.RUNTIME: "output/.runtime",
    P.PORTS: "output/.runtime/ports.json",
    P.LEDGER: "output/.runtime/sandbox_ledger.json",
    P.SSH_HOSTS: "output/.runtime/ssh_hosts.json",
    P.DEPLOY: "output/.runtime/deploy",
    P.CHECKPOINT: "output/.runtime/checkpoints/{run_id}.json",
    P.STAGING_MANIFEST: "output/.runtime/staging/{project_key}/extension-staging.json",
    P.SPILL_SESSION: "output/.runtime/spill/session-{digest}",
    P.ATTACHMENTS: "output/.runtime/attachments",

    P.OWNER: "output/{owner}",
    P.OWNER_STATE: "output/{owner}/state",
    P.OWNER_FILES: "output/{owner}/state/files",
    P.IDE_EXTENSIONS: "output/{owner}/state/ide/extensions",
    P.IDE_HOME: "output/{owner}/state/ide/home",

    P.SESSIONS: "output/{owner}/sessions",
    P.SESSION: "output/{owner}/sessions/{session_id}",
    P.SESSION_WORKSPACE: "output/{owner}/sessions/{session_id}/workspace",
    P.SESSION_LOG: "output/{owner}/sessions/{session_id}/log",
    P.SESSION_EXTENSION: "output/{owner}/sessions/{session_id}/extension",
    P.SESSION_TRACE: "output/{owner}/sessions/{session_id}/log/trace",
    P.SESSION_BASH: "output/{owner}/sessions/{session_id}/log/bash",
    P.SESSION_FLOWS: "output/{owner}/sessions/{session_id}/flows",
    P.SESSION_RUNS: "output/{owner}/sessions/{session_id}/runs",
    P.CONVERSATIONS: "output/{owner}/sessions/{session_id}/conversations",
    P.SESSION_LEGACY_EVENTS: "output/{owner}/sessions/{session_id}/events.jsonl",
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
}

__all__ = ["P", "LAYOUT", "RELATIVE", "FILES"]
