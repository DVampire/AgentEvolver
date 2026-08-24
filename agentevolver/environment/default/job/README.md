---
name: environment_default_job
description: "Background work as an ECP environment. `ENVIRONMENT.md` is the registration document; `environment.py` defines the three actions over `agentevolver.job`, which owns the registry."
version: 1.0.0
type: module
category: environment
requirements: []
metadata: {}
---
# Job environment

What this session started in the background, and the actions that control it.
`ENVIRONMENT.md` is the machine-readable registration document; `environment.py` defines
the actions. The registry — four kinds of work, one set of records — stays in
`agentevolver.job` and is unchanged.

| Path | Responsibility |
|---|---|
| `ENVIRONMENT.md` | Registration document — what is in the registry, and how to read a line |
| `environment.py` | `list` / `output` / `kill`, and the state every step renders |

## Why an environment and not three tools

Background work is **silent by construction**. A job that finished, one that failed and
one that hung look identical from outside — like nothing at all. As tools, the answer to
"what am I still waiting on" arrived only when the agent thought to ask, which asks it to
remember the thing it delegated the work in order to forget.

Half of this lesson was already recorded in the codebase. `Agent._deliver_due_reminders`:

> A reminder the agent has to remember to look for is not a reminder. `schedule` wrote
> them and `job_list_tool` could show them, but nothing pushed — so the agent saw a due
> reminder only if it happened to list its jobs, which is precisely the thing it set the
> reminder in order not to have to do.

Reminders got a push. Running jobs did not. `get_state` now renders both, every step, and
the push for reminders stays where it is: a *due* reminder is an event and rides beside
the turn's action batch, while the state is a standing picture of what is outstanding.

## What the state carries

Unfinished work only, capped at `STATE_JOB_LIMIT`. A finished job has said everything it
is going to; its output stays readable through `output`, and a line about it every step is
prompt spent on something that is over. `list` remains the whole history — the same
relation `terminal__read` has to the terminal state.

## Boundaries worth keeping straight

`kill` on a job watching a terminal stops the **watching**; the command keeps running, and
`terminal__signal` stops that. The two actions live in two environments and say so in both
descriptions, because conflating them lets "stop looking" read as "stop running".
