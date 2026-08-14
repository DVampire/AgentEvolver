---
name: job
description: "Runs work in the background and lets the agent collect it later — and holds reminders that come due — so a long command costs a step to start rather than a step spent waiting."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# Job

Runs work in the background and lets the agent collect it later — and holds reminders that
come due — so a long command costs a step to start rather than a step spent waiting.

| Path | Responsibility |
|---|---|
| `types.py` | `Job`, `JobStatus` — what a unit of background or scheduled work is, and the rules a reminder must satisfy |
| `server.py` | `job_manager` — starts, schedules, tracks, reads, and kills jobs; owns the registry |

## Why it exists

Every tool call was foreground. A measured run spent eleven minutes on one
`penguins_analysis` task, and the largest single contributor was a `bash_tool` call the
agent could only sit through: it dispatched a command, blocked, and did nothing until the
command returned. The step budget is spent on wall-clock the agent is not using.

Worse, it distorts what a step *means*. A step is meant to be a decision; a step spent
waiting is a decision the agent never got to make, and a run that spends its budget that
way looks — in the trajectory, in the reward — identical to one that thought hard and
slowly.

## Reminders are jobs

A reminder asks the same three questions as a running command — is it due, what did it
say, cancel it — so it is the same record with a different status, not a second registry:

| Question | Running command | Reminder |
|---|---|---|
| where is it | `RUNNING` | `SCHEDULED`, with `due_at` |
| is it done | exit code | due now, or due in 12m |
| what did it say | accumulated output | one line per occurrence, as it came due |
| stop it | `kill()` signals the process group | `kill()` cancels the record |

`schedule()` accepts exactly one selector: a relative `after_seconds`, an absolute
RFC 3339 `at` (an offset is required — a local time without one names no instant), or a
fixed-rate `every_seconds` of at least five minutes. `due()` reports what has come due;
`claim_due()` takes each due reminder exactly once and advances a repeating one to its
next aligned occurrence, skipping the ones that were missed rather than replaying them.

## What it is not

Not a queue, and not an executor: nothing here decides *what* runs or runs it. A reminder
comes due; it does not start work. Delivery is session-local — the registry lives in this
process and dies with it — so a reminder is a way to come back to something inside one
run, not a way to reach someone tomorrow. Anything that must outlive the run belongs in
the workspace or in a goal (`agentevolver/task/`).

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
- **A reminder is claimed once.** `claim_due()` is the delivery seam: whoever announces a
  reminder claims it first, so two pollers cannot both announce the same occurrence.
