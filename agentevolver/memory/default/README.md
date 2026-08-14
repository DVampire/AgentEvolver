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

## Compaction is a bracketed transaction

`TieredMemory` folds overflow from `recent_history` into `working_memory` one chunk at a
time. The run is bracketed: `state.compaction` is set and persisted **before** any record
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
| `ok` | every chunk summarised | shortened, summaries appended |
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
