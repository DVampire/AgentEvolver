---
status: implemented
date: 2026-08-15
owner: agent
affects:
  - agentevolver/agent/types.py
  - tests/test_run_lifecycle_wiring.py
commits:
  - 2bc75b9
---
# Agent Note: A capability with no call site is not finished, and looks finished

## Problem

Three capabilities shipped complete by every local measure and were reached by nothing.

`job_manager.forget` and `terminal_manager.forget` existed, were tested, and were called
from no production code. Both registries hold real OS resources — a backgrounded process
group, a PTY with a shell on it — and both are session-scoped by design. The only reaper
was `atexit`, which in a long-lived gateway never fires, so every finished session leaked
until the host restarted.

`job_manager.claim_due` had the same shape from the other end: reminders were written and
could be listed, but nothing delivered one. A reminder the agent must remember to check is
the errand it set the reminder to avoid.

None of this is visible from a unit test. Each piece did exactly what its tests said, and
its tests said nothing about whether anything called it. Two independent readers found the
`forget` gap by reading the code, not by running it.

## Decision

`Agent._release_session_resources` runs in `_conclude`, after the ON_STOP hooks, and
releases both registries for the session. `Agent._deliver_due_reminders` and
`Agent._announce_plan_mode` run in `_prepare_round`, beside the repeat advice.

Both release paths are best-effort and log rather than raise. A run is already over by the
time `_conclude` reaches cleanup; raising there would turn a completed task into a failed
one over a reaping error, and the caller would never learn the work was done.

Reminders and the plan notice ride on `run.action_errors` — the same channel the repeat
reminder uses. That channel is not only for errors; it is "what the model should read
before its next action". Interrupting the turn to announce either would spend a step on
something the model can simply read.

`tests/test_run_lifecycle_wiring.py` asks the question no unit test can: does the run loop
reach this at all. It reads the call graph out of the source rather than mocking, because a
mock proves the call happens in the test, not in `_conclude`.

## What this rules out

It rules out treating "the module is done and its tests pass" as done. The generalisation
worth keeping: **for anything that holds a resource or produces something for the model,
the last step is naming the line that calls it.** Three of three capabilities built in one
session had a working implementation and no caller.

It does not rule out the same gap appearing somewhere this file does not name. The wiring
test enumerates three call sites by hand; a fourth capability added tomorrow is not covered
by it.

## What would make this wrong

If a release ever needs to *fail* a run — a sandbox whose teardown must be confirmed before
the result can be trusted — best-effort is the wrong policy, and that registry needs its own
handling rather than a shared swallow.

If the volatile channel gets crowded enough that reminders, plan notices and repeat advice
compete for attention, they are no longer context, and the assumption behind "append rather
than interrupt" has stopped holding.

If a wiring check is ever written that discovers its subjects rather than listing three,
this note's second-to-last paragraph is obsolete and should be edited out.
