---
name: task
description: "Models tasks and goals — their priorities, statuses and authority — loads task documents, and resolves CLI task input into normalized records."
version: 1.0.0
type: module
category: task
requirements: []
metadata: {}
---
# Task

Models tasks and goals — their priorities, statuses and authority — loads task documents,
and resolves CLI task input into normalized records.

| File | Responsibility |
|---|---|
| `types.py` | Task and Goal contracts, enums, and goal refusals |
| `server.py` | Task records, categories, and manager operations |
| `goal.py` | `goal_manager` — the session's standing objective, and who may move it |
| `loader.py` | HTML/Markdown task document loading |
| `run_input.py` | CLI arguments and task resolution |

Task records describe work; Agent and Workflow own execution behavior.

## Tasks and goals

A task is one submission: it is created, it runs, it ends, and the run ends with it. A
goal outlives every task in the session and says what the session is *for* — it is
written to `output/<owner>/sessions/<id>/goals.json` and is still there after a restart.

The two differ in who may change them. A task is the agent's to run. A goal is the
human's to set:

| Change | Who may make it |
|---|---|
| create, edit, pause, resume | direct human only |
| complete, blocked | the agent, or a human |
| read | anyone |

An agent that may rewrite its own objective does not have a goal, it has a note: whenever
the work got hard it could edit the target down to what it had already achieved, and its
own trajectory would read as success. Reporting *progress* is the opposite case — the
agent is the only party that knows whether the objective was met or whether it is stuck —
so `complete` and `blocked` are its to claim, and a human can read the claim and overturn
it.

Authority is never a tool argument. `authority_of(ctx)` derives it from two facts the
model cannot reach: the context is not a dispatched sub-agent, and the host stamped the
run with `human_turn` when it accepted a request a human actually made. A model that
could name its own authority would have it.

Every change names the revision it read (`revision` is a compare-and-set token). A caller
working from a stale view is told so rather than silently overwriting a change it never
saw — which for a goal usually *is* the news: someone else moved it.
