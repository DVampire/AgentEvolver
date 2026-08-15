---
name: trajectory
description: "Projects agent runs into reward-annotated, step-level training records and exports formats used by supervised fine-tuning or reinforcement learning pipelines."
version: 1.0.0
type: module
category: trajectory
requirements: []
metadata: {}
---
# Trajectory

Projects agent runs into reward-annotated, step-level training records and exports formats
used by supervised fine-tuning or reinforcement learning pipelines.

| Path | Responsibility |
|---|---|
| `types.py` | Trajectory, step, context, and export contracts |
| `labels.py` | Versioned, append-only evaluator/reward facts |
| `projector.py` | Full and resumable Trace + RewardLabel → Trajectory projection |
| `server.py` | Live cache capture, label persistence, rebuild, and export facade |
| `default/` | Built-in output formats such as VERL |

Trajectory consumes lifecycle evidence but does not participate in runtime control flow.

## Schema and trace lineage

The trajectory header carries `schema_version`; each exported row also names its export
format/version. A reader accepts older files (pre-version files are schema 1) and refuses
a future schema it cannot understand. Parseable JSON is not sufficient compatibility for
training data: silently ignoring a changed reward or action field would create a valid
file with the wrong learning signal.

A trajectory is a projection, not another fact log. `project_trajectory()` reconstructs
it from committed trace events and reward-label sidecars; the hook-built JSONL remains a
low-latency cache while rebuild equivalence is verified on real runs. Each step records:

- `request_snapshot_id`: the content-addressed `model_request` trace event that produced
  the assistant decision;
- `source_trace_seq_start` / `source_trace_seq_end`: the inclusive evidence range used by
  the projection.

The trajectory header carries the full run's trace range. Retry and fallback may produce
several request events in one step; the step cites the last attempted route, while its
trace range keeps all preceding attempts inspectable. If trace retention is unavailable,
these fields remain absent rather than inventing provenance.

Code Mode sub-calls carry `parent_call_id`. They remain observations because they really
executed, but the projector excludes them from the assistant target's `tool_calls`: the
provider emitted the outer program call, not each call the program made internally.

## Reward labels

Rewards commonly arrive after execution and are not environment facts. Each call to
`set_reward()` therefore appends a versioned `RewardLabel` under `trajectory/labels/`
before updating the compatibility cache. A label names the evaluator and its version,
scope (`task` today), source trace range, timestamp, and immutable label id. Re-evaluating
a run appends a new label; it never erases the score that an older evaluator produced.

The deterministic projector selects the latest task-level label by timestamp and id.
If a label uses a newer unsupported schema, projection and export fail explicitly instead
of falling back to an older cached reward and silently changing the training supervision.
Future step/transition credit must use a new explicit granularity rather than copying one
task reward into every step and calling that transition-level supervision.

## Rebuilding and adoption

`trajectory_manager.rebuild_from_trace(events, task_id=..., adopt=False)` is the safe
verification path: it returns a rebuilt trajectory without touching the live cache.
`adopt=True` replaces and persists the cache only when the caller explicitly requests it.

SFT/RL export prefers this projector whenever the retained trace contains versioned
`model_request` events. Logs written before request snapshots cannot reconstruct the
exact messages and retain the hook-built cache as a compatibility fallback. Projection
failure is reported and also falls back: losing an old dataset is worse than exporting it
with explicitly older provenance.

### Incremental durable projection

`IncrementalTrajectoryProjector` consumes a persistence provider's `read_from(seq)` in
batches. It keeps a filtered, idempotent reducer state keyed by source `seq_no`, fsyncs
that state, and only then advances the versioned trajectory watermark. A crash between
the two writes replays the batch and deduplicates it; it cannot skip uncommitted facts.

`trajectory_manager.rebuild_incrementally(session_id, task_id=...)` exposes this path for
offline dataset construction and process restart. The manager resolves the implementation
through Trace's default `ProjectionRegistry`, so trajectory and stats share versioned
discovery and lifecycle validation while retaining storage suited to their state shapes.
`rebuild=True` explicitly discards
state and watermark after a projector upgrade or corruption. Tests require incremental
resume to be model-equivalent to `project_trajectory()` over the complete event list.
Reward labels remain separate append-only facts and are applied when the materialized
trajectory is requested, so a new evaluation does not require replaying Trace.

## Export semantics and current boundary

`to_sft_records()` emits one OpenAI-chat training row per step. The assistant target is
the same native tool-call shape used at inference, and `provenance` identifies the source
session, task, request snapshot, trace range, trajectory schema, and exporter version.
The built-in VERL adapter is intentionally text-level. It emits prompt, response, reward,
and the same provenance, while `prompt_ids`, `response_ids`, and `response_mask` remain
empty until a tokenizer-aware training provider annotates them. Task reward is currently
backfilled onto every step. Consumers that require transition-level credit assignment
must provide it explicitly; the presence of a VERL-shaped record is not a claim that the
dataset is ready for loss computation without annotation.
