---
name: environment
description: "Defines stateful execution environments and their callable actions. Each environment is one directory with an Environment subclass and an ENVIRONMENT.md; actions are exposed individually with declared or inferred parameter schemas, and results come back as the Response every capability returns."
version: 1.0.0
type: module
category: environment
requirements: []
metadata: {}
---
# Environment

An environment is somewhere with state that an agent acts on — a remote machine, a
browser, this computer's screen. It exposes **actions**, which are the things a model can
ask it to do.

| File | Responsibility |
|---|---|
| `types.py` | Environment, action, configuration, and context contracts |
| `context.py` | Registration, `ENVIRONMENT.md` parsing, and instance lifecycle |
| `server.py` | Public API: action invocation, state, schemas, and instruction text |
| `default/` | The shipped environments, one directory each |

Environment owns external state and action semantics; multi-step planning belongs to Agent
or Workflow.

## Actions are the whole interface

An action reaches a model as a native tool schema named `{environment}__{action}` —
`remote_host__run`, `browser_environment__click`. `function_callings()` produces those, and
`__call__` dispatches them.

Calling one returns a `Response`, the same `success` / `message` / `data` every other
capability returns. Actions themselves may return a plain dict because that is convenient
to write; the manager converts it at the boundary. It did not always, and both of the bugs
that came from it had the same shape — one caller read `result["message"]` when no SSH
action sets `message` and saw `None` from everything it did; another passed the dict down a
chain that takes text and the run went silent mid-task.

## One machine per tool, by design

The ordinary tools — `read_file`, `bash`, `grep_search` — act on the local sandbox, always.
No argument moves them elsewhere. Work on another machine goes through that environment's
actions instead, and data crosses only through explicit transfer actions such as SSH's
`upload` and `download`.

This is the split the shipped `ENVIRONMENT.md` files describe, and it is worth keeping for
one reason: a step that could read on one machine and execute on another looks perfectly
coherent to the model, and to anyone reading the transcript afterwards.

## What reaches the prompt

`get_instruction(allowlist)` returns each selected environment's `ENVIRONMENT.md` body —
the file where its rules and its actions' arguments are written for the model — followed by
the names its actions are callable under. The names are read from the same schemas the
model is sent, so the prose can go on saying `run` while the model is told to call
`remote_host__run`, and the two cannot drift apart.

`Agent._get_environment_context` wraps it, beside `_get_tool_context`. Every agent gets
this, not only agents that declare a primary environment.

## Adding one

See [`default/README.md`](default/README.md). Briefly: one directory, an `Environment`
subclass whose `name` is a class field, `@environment_manager.action` methods, and an
`ENVIRONMENT.md` written for the model to read.
