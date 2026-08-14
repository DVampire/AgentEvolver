---
status: implemented
date: 2026-08-15
owner: job
affects:
  - agentevolver/job/types.py
  - agentevolver/job/server.py
  - agentevolver/tool/default/schedule.py
commits:
  - a4e7705
---
# Agent Note: A reminder is a job that has not started yet

## Problem

The reference implementation ships three scheduling tools — `schedule_create`,
`schedule_list`, `schedule_delete` — as their own capability family with their own registry
beside the job registry.

Copying that shape would have given the agent two vocabularies for one idea. "What is
outstanding" would have had two answers depending on whether the outstanding thing had
started, and "stop it" two spellings. Both registries would then need the same eviction
rule, the same session scoping, and the same at-most-once discipline — written twice, and
drifting from the moment the second was written.

## Decision

Scheduling folds into `job_manager`. A reminder is a `Job` with `JobStatus.SCHEDULED`,
`due_at`, and optionally `every_seconds`; the existing tools answer all three questions
about it. `job_list_tool` is "what is scheduled", `job_output_tool` is "what did it say",
`job_kill_tool` is "cancel it".

So there is **one** new tool, `schedule_create_tool`, not three. Creating is the only verb
the job tools did not already have, because nothing else in the registry is created by the
model rather than started by a producer.

`JobStatus.is_final` had to change with it: written the obvious way it asks "is this not
RUNNING", under which every reminder is final the instant it is created and the eviction
pass deletes it before it can come due. It now names the two live states.

`claim_due()` consumes; `due()` reads freely. The split exists because delivery must be
at-most-once — see `Agent._deliver_due_reminders` — while a listing must be repeatable.

## What this rules out

A reminder that outlives the process. The registry is in memory and dies with the run, and
the tool's own result text says so rather than leaving the model to find out. Anything that
must survive belongs in the workspace or in a goal.

Also ruled out: a scheduler. Nothing here *runs* work at a time. A due reminder is text
handed to the model on its next turn; if it should trigger an action, the model takes it.

## What would make this wrong

If reminders ever need to fire without a step boundary — a timeout that must interrupt a
model call rather than wait for it to end — the delivery seam is in the wrong place, and a
real timer belongs beside the runtime pump rather than in the job registry.

If a second producer needs `SCHEDULED` to mean something else (queued behind a dependency
rather than waiting on a clock), the status is carrying two meanings and should split
before it carries three.
