---
status: implemented
date: 2026-08-15
owner: job
affects:
  - agentevolver/job/server.py
  - agentevolver/job/types.py
  - tests/test_background_jobs.py
commits:
  - e438700
---
# Agent Note: A job's first final status is the true one, so a killed job stays KILLED

## Problem

Killing a background job and the job's process exiting are two events, and they arrive in that
order. `kill()` signals the process group and records `KILLED`; some moments later the process
actually dies and the reader that was following it calls `finish()` with an exit code.

If `finish()` simply wrote what it saw, that second event would overwrite `KILLED` with
`EXITED` or `FAILED`. The agent that stopped a job would then read it back as a job that ran
to completion — and would act on its output as a result. Work the agent deliberately abandoned
becomes work it believes it did.

## Decision

`JobStatus` has an `is_final` property, and `finish()` returns immediately when the job is
already in a final state:

```python
if job is None or job.status.is_final:
    return
```

The first verdict wins. `kill()` is guarded the same way, which also makes it idempotent —
killing an already-finished job returns `False` rather than re-recording it.

This is one instance of a wider rule the job contract is built on: reading does not consume,
because an agent that polls cannot otherwise distinguish "nothing new" from "I already took
it"; a running job is never evicted, because forgetting it orphans a live process nothing can
then report on or stop; and the output cap drops the head, because a command's closing lines
are the ones that say what happened. Each of these is the same shape — the registry's answer
must stay usable by an agent that only sees the answer.

## What this rules out

**Last-write-wins on job status.** The natural implementation, and it inverts the meaning of
the one status the agent explicitly asked for.

**A separate `killed` flag beside the status.** Two fields that can disagree, and every reader
has to check both or be wrong. The status is the answer; making it authoritative is cheaper
than making it advisory.

**Waiting for the process to die inside `kill()` so there is only one write.** That makes
`kill` blocking, which is the whole thing background jobs exist to avoid, and it does not
terminate — a process ignoring SIGTERM would hang the caller.

## What would make this wrong

The exit code of a killed process is genuinely lost. Today nothing wants it; if something
needed to distinguish "killed and died promptly" from "killed and had to be forced", it would
have to be recorded as a separate field rather than by relaxing this rule.

The rule also assumes the first final status is the one the caller meant. That holds while
`KILLED` is the only status written ahead of the process's own exit. A second such
status — a timeout that abandons a job without signalling it, for instance — would need to
decide its own precedence rather than inheriting first-wins by default.
