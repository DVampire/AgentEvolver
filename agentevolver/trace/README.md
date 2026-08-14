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
| `derive.py` | Projects the surface into the message history a model would be sent |

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

The surface is the message-producing events: `agent_start` (the task), `agent_call`
(the assistant's turn), `tool_call` / `skill_call` (results), `agent_end` (the answer).
`*_start` events stay log-only — a call is part of the assistant's turn, not a message
of its own, so its arguments are joined in when projecting. Being explicit is what keeps
"this event is part of the conversation" from being a guess.

`trace_manager` maintains the **live** surface per session, because it is already the one
funnel that sees every event in order — and because a producer that wants to replace a
range needs to know what is in it, which it cannot work out from its own records.
`surface_span(session, start, end)` answers that; it returns empty when either edge is
not on the surface, so a caller cannot cite a span it does not cover. The live surface is
in-process state, so empty means "unknown", never "nothing".

The emit path is deliberately more forgiving than the fold: a malformed replacement is
kept as an append rather than dropped, because losing a live event is worse than a
surface entry in the wrong place.

The fold **refuses** a log it cannot read — an uncited replacement, a range already
replaced, a backwards range, an unknown op — rather than guessing at intent and silently
producing a history nobody wrote.

## Deriving the model's history

`derive_messages(events)` projects the surface into `[user, assistant(+tool_calls),
tool, ...]` — the shape the model was trained on, rather than the prose transcript the
prompt renders today. **Nothing calls it on the request path.** It exists so the switch,
when it is made, is a projection swap rather than a rewrite, and so its behaviour is
testable against a real trace file first.

A compaction summary rides as a `user` turn, standing for everything it shadowed — the
assistant's reasoning included, which is why `agent_call` is on the surface. Logs written
before `call_id` existed pair a result to its call by `(step, index)` instead, so an older
log still projects rather than losing its results.
