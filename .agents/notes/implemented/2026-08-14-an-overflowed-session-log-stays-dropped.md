---
status: implemented
date: 2026-08-14
owner: trace
affects:
  - agentevolver/trace/server.py
commits:
  - 8523cb5
---
# Agent Note: A session that overflows the retention cap is dropped for good, not restarted

## Problem

`TraceManager` retains a session's events in memory so consumers can project the log into a
message history. Retention needs a cap, or a long session grows without bound.

The obvious behaviour at the cap — drop the oldest events and keep going — is the dangerous
one here. What comes back is a suffix that *looks* like a whole log: it has turns, it
projects, it serializes. It has just lost its opening turns, including the task. The model
would be handed a conversation missing its beginning with nothing to indicate the loss, which
is the same failure the retention exists to avoid, arriving a few thousand events later.

## Decision

`self._max_retained = 20_000`. When a session passes it, its entry is set to `None` and a
warning is logged. `None` means "retained and then dropped", distinct from a missing key,
which means "never retained".

`events()` returns an empty list for both, and a caller that needs the whole log can tell the
two apart by looking. `Agent._derived_messages` treats an empty result as grounds to fall back
to the rendered history, loudly — which is the correct response to either state.

The entry stays `None` for the rest of the session: `_retain` checks for it and returns
without re-accumulating. Restarting retention is what produces the misleading suffix.

## What this rules out

**A ring buffer.** Produces exactly the truncated-but-plausible log described above.

**Compacting the retained events instead of dropping them.** Compaction is a real answer to
the same problem, and the log already carries compaction summaries that the surface fold
honours. But compacting *retention* would mean the trace manager deciding what a session's
history means, which is the surface's job and not the store's.

**Deleting the key on overflow.** Then "dropped" and "never retained" are the same state, and
a caller cannot report which happened.

**Raising the cap.** Twenty thousand events is already far past any observed session; a cap
that is never reached is not the problem this guards.

## What would make this wrong

The cap is a count of events, not of bytes, and events vary enormously in size — a tool result
holding a large file is one event. A session that exhausted memory well before 20,000 events
would show this to be measuring the wrong quantity.

The decision also assumes a dropped log is recoverable, which it is only because
`derive_context` falls back to the rendered history. If the projection ever became the only
path, dropping the log would end the session rather than degrade it, and this would need to
become compaction instead.
