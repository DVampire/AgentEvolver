---
name: tool_default_code_mode
description: "The run_code_tool transport: a model-written program calls the agent's tools directly, and every call it makes re-enters the agent's own guarded dispatch."
version: 1.0.0
type: module
category: tool
requirements: []
metadata: {}
---
# Code mode

The `run_code_tool` transport: a model-written program calls the agent's tools directly,
and every call it makes re-enters the agent's own guarded dispatch.

| Path | Responsibility |
|---|---|
| `run_code.py` | `RunCodeTool` — turns a `GuardedDispatch` into bindings and runs the program |
| `sdk.py` | The declarations the model reads: one signature per callable tool, plus the calling convention |

## The route a call takes

A tool called from inside a program is not called by this tool. It is called by the agent,
through the same method a wire call goes through:

```
program: await tools.write_file_tool(...)
  → child interpreter sends {"t": "call", "name": "write_file_tool", ...}
  → code_runtime serves it by invoking the binding
  → binding is GuardedDispatch.call, built by the agent for this turn
  → Agent._run_one:  trace_hook PRE_ACTION
                     plan_mode_hook  (a refusal becomes a monotonic guard denial)
                     read_only policy (same denial path)
                     _invoke_capability → tool_manager execution pipeline
                                          → immutable call snapshot
                                          → permission intent / guards / approval
                                          → timeout + tool body
                                          → normalized authoritative result
                     memory / trace / trajectory POST_ACTION
  → result text back down the same wire
```

Nothing is skipped and nothing is duplicated, because it is not a second implementation of
dispatch — it is the same method, called again.

The nested call's `root_call_id` identifies the outer model-emitted `run_code_tool` call;
`parent_call_id` identifies its immediate program. These IDs and the Tool pipeline's stable
failure code are copied into Trace. A denied binding raises inside the program, while the
durable result remains a normal failed Tool observation—Code Mode cannot mistake refusal
for an empty successful return.

Built-ins that can also be instantiated directly temporarily retain their internal
`permission_manager.check` as defense in depth. The manager-level `permission_request`
guard is authoritative for Agent/Workflow/Code Mode calls and classifies denial before
the body; the duplicate internal check protects legacy direct-instance callers.

## Why there is no fallback

Called outside an agent, the tool has no dispatch and binds nothing: a program can then
call no tool at all. The tempting alternative — fall back to `tool_manager` when there is
no agent — would be a complete second path to every tool with no permission check, no plan
mode, and no trace. It would also be invisible: the tools would work, so nothing would ever
report it.

## What a program may not call

`run_code_tool` (a program starting a program) and `done_tool`. Completion is a decision
about the run: the loop reads it from a dispatched action, so a `done` inside a program
would be answered by the program while the run carried on without it.

## Its relation to `code_interpreter_tool`

Both run model-written Python; they are not alternatives.

| | `code_interpreter_tool` | `run_code_tool` |
|---|---|---|
| what the code does | computes | calls tools |
| state between calls | persists (a kernel) | none (a fresh process) |
| figures | captured | not captured |
| use it for | analysis, plotting, exploration | batches, loops, and searches over tools |
