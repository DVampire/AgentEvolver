---
name: job
description: "Runs work in the background and lets the agent collect it later, so a long command costs a step to start rather than a step spent waiting."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# Job

Runs work in the background and lets the agent collect it later, so a long command costs a step to start rather than a step spent waiting.

| Path | Responsibility |
|---|---|
| `types.py` | `Job`, `JobStatus`, `JobHandle` — what a background unit of work is |
| `server.py` | `job_manager` — starts, tracks, reads, and kills jobs; owns the registry |

## Why it exists

Every tool call was foreground. A measured run spent eleven minutes on one
`penguins_analysis` task, and the largest single contributor was a `bash_tool` call the
agent could only sit through: it dispatched a command, blocked, and did nothing until the
command returned. The step budget is spent on wall-clock the agent is not using.

Worse, it distorts what a step *means*. A step is meant to be a decision; a step spent
waiting is a decision the agent never got to make, and a run that spends its budget that
way looks — in the trajectory, in the reward — identical to one that thought hard and
slowly.

## What it is not

Not a scheduler and not a queue. A job is started explicitly by a tool call and collected
explicitly by another; nothing here decides *when* work runs. Jobs are session-local and
die with the process — a background job is a way to overlap work within one run, not a way
to outlive it. Anything that must survive the run belongs in the workspace, written by the
job itself.

## The contract

- **Starting is not doing.** `start()` returns a handle immediately and never waits. A
  caller that wants the result asks for it.
- **A job is collected, not delivered.** Output accumulates and is read on request. The
  alternative — pushing completions into the conversation — would inject content between
  a step's decision and its result, which is the one place the history must stay ordered.
- **Reading does not consume.** `output()` may be called repeatedly and returns what has
  accumulated so far. An agent that reads early and reads again must see the earlier
  output still there, or it cannot tell "nothing new" from "I missed it".
- **A dead job keeps its output.** Exit does not clear the record; a job that failed is
  most useful precisely then.
