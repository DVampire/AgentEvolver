---
name: tool
description: "Defines atomic callable capabilities backed by Python implementations. Tool signatures are introspected into native function-calling schemas and routed through `tool_manager`."
version: 1.0.0
type: module
category: tool
requirements: []
metadata: {}
---
# Tool

Defines atomic callable capabilities backed by Python implementations. Tool signatures are
introspected into native function-calling schemas and routed through `tool_manager`.

| Path | Responsibility |
|---|---|
| `types.py` | Tool and configuration contracts |
| `execution.py` | Immutable call identity, monotonic policy, execution phases, and stable errors |
| `context.py` | Registration, dynamic loading, versions, and instances |
| `server.py` | Public schemas and the only supported execution entry point |
| `default/` | Built-in framework tools, including `inspect_tool` |
| `other/` | Optional integrations |

`inspect_tool` is one tool for all seven capability types rather than one per
type: the six it replaced asked different managers and printed a few different lines, and
everything structural about a type — which manager owns it, whether its members are
separately callable — now comes from `CAPABILITY_TYPES` instead of a branch.

A tool's prompt card carries its guidance and examples, not its parameters: those are
derived from the signature and its `Args:` docstring and travel in the request's own
`tools` array, so a prose copy beside them would be a third spelling of one contract
(`utils.string_utils.instruction_for_prompt`). `inspect_tool` returns the whole
instruction when an agent wants it.

Tools should remain small and atomic. Reusable guidance belongs to Skill; multi-step
orchestration belongs to Workflow. The former `tool/workflow/` location has been retired
(its `todo` tool now lives under `default/`); it was never a public Workflow registry, so
define Workflows in the Workflow module rather than here.

## The built-in tools

Fifty-two registered tools, grouped by what they act on. `mutates` is the registry-owned
declaration the pipeline reads at step 5: **yes** takes a pre-effect durability checkpoint,
**no** skips it, and *blank* means the tool has not declared one — which is treated as
"yes", so an undeclared mutation is never missed and the cost of silence falls on the tool
rather than on the record.

### Files and code

| Tool | mutates | What it does |
|---|---|---|
| `read_file_tool` | no | Read a file's contents. |
| `write_file_tool` | yes | Write a file, creating parents, overwriting if it exists. |
| `edit_file_tool` | yes | Replace one exact string with another. |
| `list_dir_tool` | no | List a directory as a tree. |
| `glob_search_tool` | no | Find files by glob pattern. Names, not contents. |
| `grep_search_tool` | no | Find lines by regex or literal. Contents, not names. |
| `read_image_tool` | no | Look at an image file. Needs a model that accepts image input. |
| `lsp_tool` | no | Ask a language server for a definition, references, hover type, or a file's symbols. |
| `mdify_tool` | | Convert a local file (PDF, docx, …) to markdown via markitdown. |

### Running things

| Tool | mutates | What it does |
|---|---|---|
| `bash_tool` | | One shell command, one call. Starts in the workspace. |
| `code_interpreter_tool` | | A persistent interpreter: variables, imports and open files survive between calls; figures come back as images. |
| `run_code_tool` | | A Python program that calls *your other tools* directly, so a batch of tool work costs one turn instead of one per call. |
| `terminal_open_tool` | yes | Open a terminal that stays alive between calls. |
| `terminal_send_tool` | | Type into an open terminal and read what appears. |
| `terminal_read_tool` | no | Read a terminal's output without typing at it. |
| `terminal_signal_tool` | yes | Interrupt whatever is running in a terminal. |
| `terminal_close_tool` | yes | Close a terminal and everything in it. |
| `terminal_list_tool` | no | List this session's open terminals. |
| `job_list_tool` | | List background jobs and whether each is still running. |
| `job_output_tool` | | Read what a background job has printed so far. |
| `job_kill_tool` | | Stop a running background job. |

A one-shot command, a persistent interpreter, a persistent terminal and a background job
are four different lifetimes, not four spellings of one. Job and terminal handles are
owner-fenced by Session (see above), so an id guessed from another session resolves to
nothing.

`code_interpreter_tool` and `run_code_tool` look like the same tool twice and cannot be
merged. The interpreter's code calls no tools; the program's calls are the entire point of
it, and they go back out through a `GuardedDispatch` the agent builds **for the turn that is
running** — a closure over this turn's routing table, its session/task/step coordinates and
its trace lineage, dispatched through the agent's own `_run_one` so the plan gate, the
permission check and the hook pairs all apply. A program does not get around anything.

That is why the program runs in a fresh process. The interpreter's kernel is held open
across turns and across runs, so handing it that dispatch would put this turn's authority
inside something that outlives the turn: a variable left over from an earlier call could
hold a stale dispatch, and code could stash one in a global and reuse it next turn against
a routing table it is no longer entitled to. Persistent state and turn-bound authority are
in conflict, so the two are separate tools rather than one with a flag.

Their return contracts are opposite for the same reason. The interpreter returns everything
the code produced, figures included; the program returns only what it printed or returned —
read five files, hand back the one line that matters, instead of paying for five results
someone has to read past. Saving context *is* the feature, and a tool that also promised to
show you everything would not have it.

### The web

| Tool | mutates | What it does |
|---|---|---|
| `web_searcher_tool` | no | Search the web and return a summarised report. Fans out over search providers and merges. |
| `web_fetcher_tool` | no | Visit a URL and return the page's title and markdown text. |
| `http_request_tool` | | A plain REST call (GET/POST/PUT/DELETE) returning status and body. A canvas Data node. |
| `media_search_tool` | yes | Find real images by keyword and download them locally, so a deliverable can bundle them. |
| `jina_search_tool` | | Search provider — Jina AI. |
| `serper_search_tool` | | Search provider — Google via Serper. |
| `firecrawl_search_tool` | | Search provider — Firecrawl, prioritising papers and PDFs. |
| `google_lens_search_tool` | | Search provider — Google Lens, from an image file plus optional text. |

`web_fetcher_tool` reads a page for a reader; `http_request_tool` calls an API for a
program. The four providers are registered individually and are the parts
`web_searcher_tool` is assembled from — **today it holds only `jina_search`**, the other
three being commented out of its provider table, so its `preferred` default
(`firecrawl_search`) selects nothing and it is in practice Jina plus a summarising pass.
Nothing in `configs/` mounts a bare provider, so an agent is not offered two near-identical
searches; the redundancy is in the code, not in any prompt.

### Components and evolution

| Tool | mutates | What it does |
|---|---|---|
| `inspect_tool` | no | One component's full contract — instruction, call schema — plus live registry facts: version, evolvability, source paths. All eight component types. |
| `evolution_tool` | | Manage evolved components: list active, list versions, roll back, unload. |
| `journal_tool` | yes | The evolution journal: record a hypothesis, backfill its outcome, review prior rounds. |

### Talking to someone else

| Tool | mutates | What it does |
|---|---|---|
| `ask_user_question` | no | Ask the person a concise question when you need a decision only they can make. |
| `escalate_tool` | | Ask the parent MetaAgent for guidance when blocked, then continue with its reply. |
| `reply_tool` | | Reply to a sub-agent that escalated, unblocking it. |
| `report_tool` | no | Report a finding upward without waiting for a reply. |
| `send_message_tool` | yes | Give a continuable background sub-agent more work on the same conversation. |

Five directions, not five spellings: the person, the parent, a blocked child, a one-way
note upward, and more work for a child that is still running.

### Intent and state

| Tool | mutates | What it does |
|---|---|---|
| `create_goal_tool` | yes | Record the standing objective a human asked for — what the whole session is for. |
| `get_goal_tool` | no | Read the goal, where it stands, and its current revision. |
| `update_goal_tool` | yes | Report progress, or apply a change a human asked for. Names the exact revision it read. |
| `todo_tool` | | Manage `todo.md`: decompose a task, track the steps. |
| `schedule_create_tool` | yes | Set a reminder that comes due later in this run — after a delay, at a time, or on an interval. |
| `exit_plan_mode` | no | Present a finished plan for approval and leave plan mode if it is approved. |
| `done_tool` | | Declare the task complete. What an evolution run puts its artifact path in. |

A goal is what a person asked for; a todo is your own plan of work. Goal is split across
three tools because `update` names the revision it read — an optimistic-concurrency check
that a single read-modify-write tool could not express.

### Past runs

| Tool | mutates | What it does |
|---|---|---|
| `session_search_tool` | no | Find past runs whose recorded work matches a description. |
| `session_read_tool` | no | Read one past run step by step, a line per step. |
| `session_event_search_tool` | no | Find individual recorded steps, in one run or across all of them. |
| `session_event_read_tool` | no | Read one recorded step in full, with its neighbours. |
| `session_trace_tool` | no | Show which runs belong together — a run and the sub-agent runs it spawned. |

Search and read, at two grains: the run and the step.

### Everything else

| Tool | mutates | What it does |
|---|---|---|
| `git_tool` | | Git operations inside the project's `workspace_root`. |
| `deploy_tool` | yes | Deploy and manage web services (static/SPA/API) in isolated sandboxes, each on its own URL. |
| `reformulator_tool` | | Reformulate a clean final answer from a conversation transcript. Lives in `other/`, not `default/`. |

## One execution path

Every Tool invocation—native function calling, Workflow, direct SDK use, and a Code Mode
sub-call—enters `tool_manager(...)` and traverses this order:

1. Resolve the registered tool and snapshot its version.
2. Create `ToolExecution`: a registry-owned token plus call/root/parent IDs,
   session/task/agent/step coordinates, and canonical JSON arguments.
3. Bind the arguments to the Python signature. A malformed model call becomes
   `invalid_arguments`; the body is not entered.
4. Apply Agent-supplied denials and registered monotonic guards in order. A guard may
   abstain, deny, or ask; it cannot force-allow. ASK without a working approval resolver
   is denied.
5. After guards and any approval settle, checkpoint policy/approval facts for a Tool
   whose `mutates` is not explicitly `False`.
6. Invoke the body inside the tool's declared timeout.
7. Normalize the outcome. A raised exception, timeout, non-`Response` return, or a
   tool-reported failure each has a stable `error_code`.
8. Apply ordered result processors. They may enrich, redact, or turn success into
   failure, but may never turn a failure into success.
9. Finalize output once: oversized text is spilled and replaced by a bounded excerpt.
10. Attach the frozen execution summary and notify result observers. Observer failure is
   logged and cannot rewrite what the caller receives.

The public return remains `Response`. Its `extra["execution"]` contains the versioned
machine contract: identity, tool version, phase, duration, timeout, success, and error
code. Agent copies this record into the matching `tool_call` Trace event, so a dataset or
UI never has to infer “timeout versus permission denial” from English text.

### Tools act on the local sandbox

`read_file`, `write_file`, `edit_file`, `list_dir`, `grep_search`, `glob_search`, `bash`,
the persistent terminals and background jobs — all of them act here, always. No argument
sends one of them to another machine.

Work on a remote machine goes through that environment's actions instead
(`remote_host__run`, `remote_host__read`), and data crosses only through explicit transfer
actions. Keeping the two apart is what stops a single step from reading on one machine and
executing on another — which reads as one coherent task in the transcript and is not.

Job and terminal handles are owner-fenced by Session, so guessing an id from another
Session cannot expose output or signal its process.

### Permission intent

Generic policy cannot safely infer meaning from names such as `path`, `text`, or
`command`. A Tool that maps to the common permission system overrides
`permission_request(arguments, ctx)` and returns the exact `PermissionRequest` its
implementation understands. Tool Manager evaluates it as a call-local monotonic guard
before entering `__call__`; a broken intent builder fails closed as `guard_error`.

The bash, read/write/edit file, image read, LSP, and terminal open/send tools declare this
intent today. Their existing internal checks remain temporarily as defense in depth for
legacy code that invokes a Tool instance directly. New runtime paths must call
`tool_manager`, not `Tool.__call__`, so policy, timeout, spill, error codes, and Trace
lineage cannot be bypassed together.

### Monotonic policy

Extensions register with `tool_manager.guard(...)` and receive a frozen
`ToolExecution`. Its `arguments` property returns a detached copy, so a guard cannot
change the call seen by later guards or the body. Return `None`/`abstain()` to add no
restriction, a string/`deny(reason)` to refuse, or `ask(reason)` for one-shot human
approval. There is intentionally no `allow()` decision: adding a guard can only preserve
or reduce authority.

Registration returns the exact disposer. This matters for tests, hot reload, and scoped
runtime ownership—removing one extension does not clear unrelated policy.

Postprocessors use `tool_manager.postprocess(...)`; immutable result observers use
`tool_manager.observe(...)`. The Gateway installs its real, one-shot rendezvous with
`tool_manager.set_approval_resolver(...)`; the returned identity-safe disposer prevents an
older Gateway shutdown from clearing a newer resolver. Without an integration, ASK still
fails closed as `approval_unavailable`.

The pre-effect durability checkpoint now lives inside the authoritative Tool pipeline.
This is later—and safer—than Agent dispatch: PRE_ACTION, plan/read-only denials, Tool-owned
permission intent, extension guards, and human approval have all settled, but `__call__`
has not begun. The registry-owned `mutates` declaration decides whether it is needed.
The pipeline does not catch `TraceIntegrityError`, and Agent explicitly propagates it, so
a training/high-risk failure reaches the run boundary. Calling `Tool.__call__` directly
remains a legacy path and provides neither approval nor this durability guarantee.

## Failure codes

| Code | Meaning | Did the body run? |
|---|---|---|
| `not_found` | Registry has no such Tool | no |
| `invalid_arguments` | Call does not bind to the implementation signature | no |
| `policy_denied` | Agent policy or monotonic guard refused it | no |
| `approval_unavailable` / `approval_denied` | Consent was required but not obtained | no |
| `guard_error` | A guard failed; the call failed closed | no |
| `timeout` | Call exceeded `call_timeout_seconds` | possibly |
| `execution_error` | Tool body raised | yes |
| `tool_reported_error` | Tool returned `Response(success=False)` | yes |
| `invalid_result` | Body returned something other than `Response` | yes |
| `postprocess_error` / `finalization_error` | Result policy/finalization contract failed | yes or previously settled |

`Tool.call_timeout_seconds` declares what one call of the tool is allowed to cost. The
dispatch funnel reads it from the registry, so the budget sits next to the code that knows
the work; a tool that declares nothing takes the manager default. A tool that also bounds
something internally (`bash_tool.timeout` bounds the child process) should keep the inner
bound smaller, so it returns its own diagnostic rather than being cut off mid-report.
