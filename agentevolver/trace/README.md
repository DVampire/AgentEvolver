---
name: trace
description: "Captures structured lifecycle events, persists them, and fans them out to subscribers."
version: 1.0.0
type: module
category: trace
requirements: []
metadata: {}
---
# Trace

Captures structured lifecycle events, persists them, and fans them out to subscribers.

| Path | Responsibility |
|---|---|
| `types.py` | Trace event contracts and event factories |
| `writer.py` | Durable event writing |
| `server.py` | Trace manager lifecycle and the subscriber fan-out |
| `surface.py` | The surface fold: which events still stand for the history |

Trace owns no UI of its own. Events reach a browser through `subscribe()` —
the Gateway forwards them to the web frontend — and are persisted as
`<log_root>/trace/<session_id>.jsonl` for offline inspection.

Trace is observational. It must not change Agent, Runtime, or Workflow execution semantics.

## Sequence numbers

`trace_manager.emit` stamps `seq_no` — the event's position in its session's log —
before the queue or any subscriber sees it. Numbering in the writer instead would leave
subscribers holding events whose position was still unknown, and a session reopened in a
new process continues from the writer's index rather than restarting at 0, so no two
events ever claim the same position.

The number exists so one event can cite another. "Somewhere earlier in the file" cannot
express a summary naming the range it replaced.

## The surface

The log is append-only and only grows. What the history *says* does shrink — compaction
folds a run of records into one summary. A surface reconciles the two without deleting
anything:

| `surface_op` | Meaning |
|---|---|
| absent | log-only: a step marker or a bookkeeping record that never stood for history |
| `"append"` | joins the history at the tail |
| `{"op": "replace", "start": s, "end": e}` | stands in place of the surface entries `s`..`e` |

`fold_surface(events)` replays the declarations and returns the current history order.
The replaced events stay in the log exactly as written — a summary **shadows** its
originals rather than deleting them, and must cite every seq it shadows, so the way back
from a summary to what it summarised is always in the record.

Only the four constructors whose events become history records join the surface
(`agent_start`, `agent_end`, `tool_call`, `skill_call`); step markers and `*_start`
events are log-only. Being explicit is what keeps "this event is part of the
conversation" from being a guess.

The fold **refuses** a log it cannot read — an uncited replacement, a range already
replaced, a backwards range, an unknown op — rather than guessing at intent and silently
producing a history nobody wrote.
