---
status: implemented
date: 2026-08-14
owner: agent
affects:
  - agentevolver/agent/types.py
commits:
  - 8523cb5
  - ce727ae
---
# Agent Note: The capability catalog is frozen at its first render and changes are announced after it

## Problem

The capability catalog — tools, skills, connectors, workflows — is rendered from the live
managers on every step. That is fine right up until this framework does the thing it exists
for: evolution registers a component mid-session, every agent's next step reads the managers
again, and the catalog is rebuilt from scratch rather than appended to.

Rebuilt is the operative word. Measured on the real registry, removing one skill of
eighty-four leaves a common prefix of **four characters**. The catalog sits at the front of
the user turn, ahead of the cache breakpoint, so a rewrite there invalidates the cached
prefix for the whole conversation behind it. Self-evolution would have cancelled the prompt
caching it had just been given, and would have done it silently — the only symptom is a
cache-read counter that stops moving.

## Decision

`Agent._freeze_capabilities` stores the first render's catalog bytes in
`ctx.extra["_capability_snapshot"]` and sends **those** bytes for the rest of the session.
What has changed since is stated separately, in a `<capability-context-changes>` block
appended to the end of the same turn — past `</capability-context>`, which is where the
cache breakpoint sits, so changing it costs nothing.

The diff is taken per leaf block and announced in the same vocabulary the catalog uses: an
added skill appears inside a `<skill-context>`, an added tool inside a `<tool-context>`.
Withdrawals are announced too (`no longer available, do not call:`), because a component
that was optimized away and is still being called is its own failure.

Leaf blocks are matched with a negative lookahead that excludes the container **while**
matching, not after: `<capability-context>` matches the same `[a-z-]+-context` pattern and,
being outermost, wins the lazy match. Filtering it out afterwards discards every leaf inside
it and the diff comes back empty — which is a defect this code already had once.

Both paths freeze. `_derived_messages` freezes as part of restructuring the turn;
`_frozen_rendered` does the same on the default path by substring surgery on the rendered
text, without restructuring anything. Freezing originally reached only the projection, which
is off by default — so on the path everything actually runs, nothing was frozen at all.

## What this rules out

**Re-rendering and letting the cache sort it out.** It cannot. A prefix cache keeps a
prefix; one changed character near the front discards everything after it. Measured before
the catalogs moved ahead of the agent state, `cache_read` was zero on every step.

**Appending new entries to the catalog in place.** Would work if the catalog were built by
appending, and it is not — it is a sorted render of a live registry, so a single removal
reorders and reflows it.

**A free-standing change log with its own vocabulary.** Rejected: it asks the model to merge
a catalog and a change log to answer "what skills do I have", using a concept the prompt
never defined. The announcement reuses the block types the catalog already uses; the block
repeats, the concept does not.

**Rewriting the catalog when it drifts too far.** Not ruled out — see the
[re-freeze thresholds](2026-08-14-refreeze-past-a-ratio-and-an-absolute-floor.md), which is
exactly that, bounded.

## What would make this wrong

If the catalog moved behind the cache breakpoint — after the agent state rather than before
it — freezing would buy nothing, because the bytes would already be outside the cached
prefix. The freeze is only worth its complexity while the
[catalogs-first layout](2026-08-14-catalogs-ahead-of-the-agent-state.md) holds.

If a provider shipped a cache that keyed on content rather than on prefix, the whole
construction is unnecessary.

And if a session ever accumulates enough changes that the announcement rivals the catalog it
patches, the model is being asked to reconcile two documents on every step. That is what the
re-freeze thresholds are for, and those thresholds have never been observed firing on a real
evolution session — they were set from a cost argument, not from data.
