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
| `request.py` | Versioned, content-addressed model request snapshots |
| `integrity.py` | Durability boundaries and fail-open/fail-closed integrity profiles |
| `recovery.py` | Crash-recovery checkpoint derivation and effect reconciliation |
| `writer.py` | Durable event writing |
| `persistence.py` | Persistence protocol and indexed SQLite provider |
| `projection.py` | Versioned projector registry and monotonic projection watermarks |
| `stats.py` | Crash-safe operational statistics derived from committed events |
| `server.py` | Trace manager lifecycle and the subscriber fan-out |
| `surface.py` | The surface fold: which events still stand for the history |
| `derive.py` | Projects the surface into the message history a model would be sent |

Trace owns no UI of its own. Events reach a browser through `subscribe()` —
the Gateway forwards them to the web frontend — and are persisted as
`<log_root>/trace/<session_id>.jsonl` for offline inspection.

Trace is observational under the default `interactive` profile. A caller that explicitly
selects `training` or `high_risk` promotes durable evidence into a precondition: execution
stops at a semantic boundary when Trace cannot prove that preceding facts were committed.
This fail-closed behavior is part of those profiles' public contract, not an accidental
logging side effect.

## Format and compatibility

Every event carries `schema_version`, the version of the common trace envelope, and an
`ignorable` compatibility declaration. These answer different questions:

- an envelope version says whether a reader understands fields such as sequence and
  surface provenance;
- `ignorable` says whether dropping an unknown event preserves the meaning of every
  projection the reader intends to build.

Old event lines have neither field and validate as envelope version 1, non-ignorable.
That conservative default is deliberate: a reader may reject evidence it cannot
understand, but must not silently produce a plausible, incomplete training record.

## Model request snapshots

A `model_request` event is committed immediately before each provider attempt. It records
the requested alias, the route actually attempted (including fallback), provider/model,
effective behavioural parameters, response format, effective provider-bound messages,
complete tool schemas, and request-pressure metadata. `task_id`, `agent_name`, and
`step_number` join it to the agent lifecycle. When pressure pruning applies, the original
tool result remains in earlier Trace events while the snapshot records the exact excerpt
the model actually saw; calling the excerpt “complete messages” would be incorrect.

The snapshot is immutable and content-addressed by `snapshot_id`. Changing a prompt,
tool schema, routed model, or effective parameter changes that id; a trajectory cites the
id instead of copying the request into a second source of truth.

`trajectory.project_trajectory()` consumes these request events together with action,
result, assistant-turn, and terminal events. This is the correctness path that proves a
trainable record can be recreated after its hook-built cache is deleted.

Calls made inside Code Mode carry `parent_call_id` on their start and result events. This
preserves the execution tree and prevents a projector from mislabelling program-internal
calls as actions emitted directly by the model.

Each completed Tool result also carries `metadata.execution`, copied from the authoritative
Tool pipeline outcome. It includes a schema version, registry-owned execution token,
call/root/parent IDs, tool name/version, session/task/agent/step coordinates, terminal
pipeline stage, duration, timeout, and stable error code. The human-readable `error` remains
for debugging; training filters and analytics should branch on the code rather than parse
phrases such as “timed out” or “permission denied”. A policy-refused Tool still emits this
result and pairs with its start event even though its body never ran.

The execution identity also contains the selected `world`: provider kind/name and
workspace root, plus a fingerprint for remote targets. SSH addresses, usernames, jump
hosts and credential paths are deliberately absent. This lets a dataset distinguish local
and remote rollouts without turning infrastructure topology into training data.

Credentials are never fields of a snapshot. The API endpoint is stored only as a SHA-256
fingerprint: this distinguishes deployments for lineage without publishing a URL that may
contain user information, query credentials, or internal topology. Message content is
preserved because it is the training evidence itself and must be handled with the same
privacy policy as every trajectory.

The model layer emits this event and makes it durable before dispatch. Under `interactive`, an
unavailable/slow writer records degradation when possible and does not disable inference.
Under `training` or `high_risk`, failure raises `TraceIntegrityError` before any provider
request is sent; retry and fallback explicitly do not catch that error because another
route cannot repair missing source evidence.

## Sequence numbers

`trace_manager.emit` stamps `seq_no` — the event's position in its session's log —
before the queue or any subscriber sees it. Numbering in the writer instead would leave
subscribers holding events whose position was still unknown, and a session reopened in a
new process continues from the writer's index rather than restarting at 0, so no two
events ever claim the same position.

The number exists so one event can cite another. "Somewhere earlier in the file" cannot
express a summary naming the range it replaced.

## Durability boundaries and integrity profiles

`ensure_trace_durable()` is used at three meaning-bearing boundaries rather than on a timer:

1. after the exact `model_request` snapshot is emitted and before provider dispatch;
2. after PRE_ACTION facts/policy settle and before any Tool whose `mutates` is not
   explicitly `False` may enter its body; this runs inside Tool Manager after any human
   approval response has been emitted, so consent is part of the flushed evidence;
3. after all POST_STEP hooks have emitted the completed step.

The profile resolves explicit call value → `ctx.extra["trace_integrity_profile"]` → global
config. `interactive` preserves availability: a timeout or exception emits one
non-ignorable `custom` event with `metadata.kind="integrity_degraded"` per
Session/boundary/issue, then continues. `training` and `high_risk` require an active Trace
writer and raise `TraceIntegrityError`; read-only tools do not pay for the pre-effect
flush.

Queue drainage alone is not called durability. `emit()` now reports whether the event
entered the bounded persistence queue. A queue overflow records a permanent Session gap,
and both JSONL and SQLite providers retain the first write failure even after later events
succeed. The first known gap is sealed under `<trace_root>/integrity/` with fsync and atomic
replacement, so restarting the process cannot make the same Session appear complete.
`flush()` followed by either condition still fails a strict durability boundary: missing evidence
cannot be recreated by waiting longer. This also means a typo in profile naming is refused
rather than interpreted as interactive.

This is deliberately separate from `recovery.py`. Integrity decides whether preceding
events are safely persisted before execution advances; recovery projects those durable
events into one `ExecutionCheckpoint` after interruption. Only the latter is a checkpoint.

Tool approvals are non-ignorable `custom` facts with
`metadata.kind="tool_approval"`. They preserve request/response/timeout/cancellation,
approval id, execution token and call id, while the public input contains only Tool/world
identity, argument names and a canonical argument digest—not raw values. A projector can
therefore prove which immutable call was approved without copying secrets into training
data. An unknown reader must reject rather than silently erase the human policy decision.

## Incremental reads and projection watermarks

Both persistence providers implement `read_from(session_id, after_seq=..., limit=...)`.
JSONL stays the human-inspectable default and streams without loading the complete file,
although it must scan lines to reach a sequence. SQLite stores `(session_id, seq_no)` as
the primary key, rejects accidental overwrite, maintains a session summary table, and
uses an indexed range query. Select it with
`trace_manager.initialize(..., persistence="sqlite")`; readers and projectors use the
same `TracePersistence` contract either way. Legacy JSONL logs without `seq_no` use
zero-based line positions through the incremental API.

`TraceManager.read_from()` additionally validates each envelope with
`parse_trace_event()`. Durable reads include only flushed data; callers that just emitted
events must `await trace_manager.flush()` before advancing derived state.

`ProjectionWatermarkStore` persists one cursor per `(projection, projection_version,
session_id)` under `<trace_root>/projections/`. Writes use fsync plus atomic replacement,
watermarks can only move forward, corrupt files fail visibly, and a projector version
change raises `ProjectionVersionMismatch` so the caller must rebuild its derived data.
The consumer must write its projection output durably *before* calling `advance()`; this
ordering makes a crash replay events rather than skip uncommitted output.

`ProjectionRegistry` is the common discovery and construction path. Built-ins are
available from `get_default_projection_registry()` as `trajectory` and `stats`; a custom
consumer registers one stable name, positive implementation version, and runner factory.
Duplicate names fail instead of silently changing which implementation owns an existing
watermark. Replacing a registration requires a version bump, after which old state raises
`ProjectionVersionMismatch` until the operator explicitly rebuilds it.

### Operational statistics

`TraceStatsProjector` reads only committed suffixes and keeps one compact state checkpoint
per session. It atomically writes and fsyncs that state before advancing the `stats`
watermark. If the process dies between those writes, restart observes the state ahead of
the watermark and reconciles the cursor without counting the same events twice; state
behind a watermark is refused because it would mean data was skipped.

The projection reports event counts, agent/task identities, routed model/provider counts,
tool and skill outcomes, per-event duration totals, errors, and terminal outcome. `usage` sums `agent_call` step usage. The independently
reported `agent_end` total is retained as `reported_run_usage` for reconciliation rather
than added a second time. A difference between those values is evidence of incomplete
step capture, not a reason to hide it with arithmetic.

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
