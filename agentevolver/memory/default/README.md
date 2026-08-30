---
name: memory_default
description: "Provides filesystem, general, and tiered memory implementations. Each system follows the parent Memory contracts while choosing its own persistence and retrieval strategy."
version: 1.0.0
type: collection
category: memory
requirements: []
metadata: {}
---
# Built-in memory systems

Provides filesystem, general, and tiered memory implementations. Each system follows the
parent Memory contracts while choosing its own persistence and retrieval strategy.

## Active context is checkpoint + exact tail

The model-facing conversation has four ordered layers: a stable system/task prefix, one
canonical checkpoint, the newest complete assistant/tool turns, and the live per-step
state. Trace is the source of truth; `MemoryRecord` is only a retention index and UI
projection. A closed assistant turn receives an index even when it calls no tool, so
retention never means "the last N tool results" by accident.

Compaction replaces old complete turns on the Trace surface. Responses routes keep the
opaque item returned by the provider's compaction endpoint and replay it unchanged. Chat
routes use the readable checkpoint generated from the same source turns. Both forms live
on one `CompactionMessage`, which prevents provider state from being flattened into prose
while keeping the run inspectable and portable.

The primary trigger is mutable token growth after the latest checkpoint. Complete-step
count, uncached growth, total request size, and provider context pressure are independent
safety triggers. The exact tail is deliberately small; it is a protocol-safety window,
not the main memory store.

Conversation memory remains session-scoped. Cross-session learning belongs to the
versioned capability/evolution pipeline, so one benchmark task cannot silently leak its
solution into another task's prompt.

## Compaction is a bracketed transaction

`TieredMemory` folds old closed turns from `recent_history` into one replacement
`working_memory` checkpoint. Internal summarizer input may be chunked, but the latest
summary supersedes the previous one instead of creating an ever-growing summary list.
The run is bracketed: `state.compaction` is set and persisted **before** any record
leaves `recent`, and cleared only after the last chunk is summarised and written.

Clearing it last is the point. A snapshot still carrying the bracket says a compaction
started and did not finish, so the history below it may be short by whatever that run had
taken. Clearing it first would make a crashed compaction look exactly like a completed one.

Both artifacts surface it: the JSON carries a `compaction` object, and the HTML renders a
warning **above** the empty-history branch — a compaction that died after emptying `recent`
otherwise renders as "No history yet", which is the most misleading thing memory could say.

This is a diagnostic, not recovery. These files are written and never read back, so nothing
resumes an interrupted compaction; what the bracket buys is that the gap stops being silent.

Outcomes are distinguished rather than collapsed into one "compaction failed" line:

| Outcome | What happened | History |
|---|---|---|
| `ok` | every chunk summarised | shortened, latest checkpoint replaced |
| `empty` | the summariser had nothing to say | restored untouched |
| `summary` | the summariser raised — model unreachable, bad response | restored untouched |
| `cancelled` | the task was cancelled | restored, and the cancellation propagates |
| `failed` | anything else | whatever completed is kept |

A chunk is summarised before the next is taken, and a chunk whose summary does not arrive is
put back, so an interrupted run leaves history shorter but never holed.

## The size backstop keeps both ends

`_append_recent` bounds one entry at `_RECORD_DETAIL_MAX`, keeping a head **and** a tail.
Head-only truncation drops whatever a producer appended last, and what the tool pipeline
appends last is the spill locator — so a spilled result would reach memory as an excerpt
announcing that text was dropped while no longer saying where it went.
