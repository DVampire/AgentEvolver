---
name: session_query
description: "Reads the trace logs of runs that already finished, so an agent can find what a past run tried and what it got back instead of starting blind."
version: 1.0.0
type: module
category: session
requirements: []
metadata: {}
---
# Session Query

Reads the trace logs of runs that already finished, so an agent can find what a past run
tried and what it got back instead of starting blind.

| Path | Responsibility |
|---|---|
| `types.py` | `SessionRecord`, `EventHit`, `SessionHit`, `SearchPage`, `SessionOutline`, `EventWindow` — the views a bounded read returns |
| `server.py` | `session_query` — discovers logs, summarises, searches, reads one event with its links |

## Why it exists

Every run already writes a complete record of itself. `trace` persists one JSONL file
per run under `output/<owner>/sessions/<project>/log/trace`, and until now nothing read
one back. A system whose whole premise is self-improvement could not answer "have I done
this before" — the evidence sat on disk, and each run repeated the last one's dead ends
at full price.

## The unit is a run, not a session directory

One session directory holds several logs: the run the session was opened for, and one per
sub-agent it spawned, each under its own trace session id. `f49d6082` delegates and
records that it delegated; `code_agent-1edbf044`, filed beside it, records what was
actually done. Retrieval is per file, because the delegate's log is usually the one
holding the work.

That co-location *is* the lineage available here, and `related()` returns it.
`parent_session_id` in an `agent_start` names the parent **agent runtime**, not a trace
session id, so it does not resolve to one; it is reported as recorded and not treated as
a link.

## The contract

- **Read-only.** Nothing opens a log for append, rewrites an event, or deletes a file.
  A query cannot damage the record it queries.
- **Every answer is bounded, and says so.** Each method caps what it returns and reports
  `truncated` / `total`. A capped result presented as a complete one is how an agent
  concludes that work was never done.
- **A partly-broken log still reads.** A line that does not parse is counted and carried
  in `unreadable_lines`; a log whose replacements no longer fold falls back to write
  order and says which reading was used. Refusing the file would hide the session someone
  is investigating precisely *because* it is odd.
- **Rows come back as written.** Reads return the stored dicts, not validated
  `TraceEvent`s. Validating would let one field added by a later version make a whole log
  unreadable, and the durable record is the thing being asked about.

## Two granularities of search, and they are not the same rule

`search_sessions` matches a run when every term appears **somewhere in it** — a
description like "penguins EDA matplotlib" is spread across the task, the tool calls, and
the answer, so requiring one event to carry all of it finds nothing. `search_events`
requires every term in the **same event**, because its answer is a coordinate to read
from and a half-matching hit sends the caller to the wrong step.

## Two readings of one log

`outline(surface_only=True)` folds the surface: a compaction summary stands in place of
the events it replaced, which is the history that run's own agent saw. `surface_only=False`
gives the raw append order — everything that happened, summaries and shadowed originals
alike. `event_window` carries both directions of that link, so from a summary there is
always a way back to what it summarised.

## What it is not

Not an index and not a cache. Every call reads the files it needs; there is no database
to keep in step with the logs, and nothing to rebuild after a crash. That costs a linear
scan, which is why `MAX_RUNS_SCANNED` exists — and why a query that needs to be fast
belongs behind a real index rather than behind a cache bolted onto this one.

Not a writer, and not a memory. What an agent should *carry forward* from a past run is
`memory`'s decision; this only makes the past legible.
