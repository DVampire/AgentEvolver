---
name: code
description: "Runs a model-written program in its own interpreter and bridges the calls it makes back into this process, so a batch of tool work costs one turn instead of one turn per call."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# Code

Runs a model-written program in its own interpreter and bridges the calls it makes back
into this process, so a batch of tool work costs one turn instead of one turn per call.

| Path | Responsibility |
|---|---|
| `types.py` | `CodeRunResult`, `CodeFailure` — what a run produces; `GuardedDispatch` — the one way a program reaches anything outside itself |
| `server.py` | `code_runtime` — spawns the child, serves its calls, settles the run |
| `bootstrap.py` | The child half: runs the program, forwards its prints, asks the host for each call |

## Why it exists

A turn is expensive: the model emits one call, waits, reads the result, emits the next.
Reading three files is three turns and three round trips, and every intermediate result
lands in the context whether the model needed it or not. A program that reads three files
is one turn, and only what it prints or returns comes back.

The other half of the reason is that models write more code than they write tool-call
traces, so a loop, a branch or a join is expressed in the notation they are fluent in
instead of being spread across turns that each have to re-derive the plan.

## What runs where

The program does not run here. It runs in a fresh `python -u bootstrap.py`, and its only
way out is a JSON line asking the host to call a name. That is not for defence against a
hostile program — the child is the same user on the same filesystem, exactly like
`bash_tool` — it is so the program's world contains **no framework objects**. Running it
in this process would put `permission_manager`, the hook registry, and every live tool
instance one `import` away from code the model wrote, and a program could turn the guard
off before calling the thing it guards.

## The contract

- **The runtime knows nothing about tools.** It is handed named async functions and
  calls them. What those functions do — and what they check first — belongs to the
  caller. `GuardedDispatch` names that arrangement without the runtime depending on it.
- **A binding that fails does not fail the run.** The call raises `ToolCallError` inside
  the program, which may catch it and carry on. Only the program itself ending badly is
  a failure of the run.
- **Output travels as it is printed.** A program killed at its time budget still returns
  what it had printed by then, which is usually how far it got.
- **A run leaves nothing behind.** One process per run, killed if it will not exit. There
  is no state to carry across calls, which is what keeps a run reconstructable from its
  own record — the opposite trade from `code_interpreter_tool`'s persistent kernel.
- **The peer is assumed hostile.** It runs model code. Unparseable lines are ignored,
  unknown binding names are refused, and a reply that arrives after the program ended
  goes nowhere.

## What it is not

Not a sandbox, not a kernel, and not a second way to call a tool. A program that has no
`GuardedDispatch` can call nothing at all — the binding table is empty, and every call
raises. There is deliberately no fallback that reaches the tool manager directly.
