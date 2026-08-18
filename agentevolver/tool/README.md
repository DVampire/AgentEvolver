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
| `default/` | Built-in framework tools, including `inspect_capability_tool` |
| `other/` | Optional integrations |

`inspect_capability_tool` is one tool for all seven capability types rather than one per
type: the six it replaced asked different managers and printed a few different lines, and
everything structural about a type — which manager owns it, whether its members are
separately callable — now comes from `CAPABILITY_TYPES` instead of a branch.

A tool's prompt card carries its guidance and examples, not its parameters: those are
derived from the signature and its `Args:` docstring and travel in the request's own
`tools` array, so a prose copy beside them would be a third spelling of one contract
(`utils.string_utils.instruction_for_prompt`). `inspect_capability_tool` returns the whole
instruction when an agent wants it.

Tools should remain small and atomic. Reusable guidance belongs to Skill; multi-step
orchestration belongs to Workflow. The former `tool/workflow/` location has been retired
(its `todo` tool now lives under `default/`); it was never a public Workflow registry, so
define Workflows in the Workflow module rather than here.

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
