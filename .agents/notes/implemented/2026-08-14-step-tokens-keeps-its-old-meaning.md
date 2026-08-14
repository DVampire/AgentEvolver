---
status: implemented
date: 2026-08-14
owner: agent
affects:
  - agentevolver/agent/types.py
  - agentevolver/model/types.py
  - agentevolver/trace/types.py
  - agentevolver/trajectory/server.py
commits:
  - 95c4d63
---
# Agent Note: `step_tokens` keeps meaning output tokens, and full usage travels beside it

## Problem

`Agent._think` kept `output_tokens` from the provider's usage record and discarded the rest —
input, cache reads, cache writes, cost. That is the first hop out of the model layer, so
nothing downstream could ever recover them: no durable record could tell a cache hit from a
full re-read, and the caching defect that motivated all of this was therefore invisible to
every measurement the framework took of itself.

The obvious repair is to widen `step_tokens` into the whole record. It is also the wrong one.
`step_tokens` is read by `TrajectoryServer.close_step` as `step.token_usage`, an int, and the
trajectory's reward is computed from it. Changing what the name means changes what the reward
means, silently and retroactively — every trajectory recorded before the change would be
comparable to none recorded after.

## Decision

`step_tokens` stays an int and stays `usage.output_tokens`. The full `TokenUsage` travels
alongside it as `step_usage`, a separate key on the same decision dict, and is carried through
`_post_step` into three places:

- `TrajectoryStep.usage`, beside the existing `token_usage` int.
- `TraceEvent.usage`, a first-class field, emitted per step and rolled up per session at
  `agent_end`.
- `scripts/context_baseline.py`, which can now report what was billed rather than only the
  prefix-reuse proxy.

A step the provider did not report is skipped, not counted as zero. Zero and unreported are
different facts, and conflating them is exactly the failure mode described in the
[cache-counter spellings](2026-08-15-cache-counters-have-four-spellings.md) note.

## What this rules out

**Redefining `step_tokens` as total tokens.** It would rewrite the reward signal without
touching the reward code — the worst kind of change, because nothing fails and every number
shifts. Old and new trajectories would be silently incomparable.

**Dropping `step_tokens` in favour of `step_usage` alone.** Cheap to do and it breaks the
trajectory reader, which wants an int. The int is not vestigial; it is a consumed contract.

**Reconstructing usage downstream from the prompt.** Prefix reuse measured from prompt files
is a proxy, and the two came apart once already — which is precisely what went unnoticed.

## What would make this wrong

If the reward stopped reading `step.token_usage`, the reason to keep two fields disappears and
they should collapse into one. That is the single observation to watch for; the whole
justification is one consumer.

If a provider reported output tokens in a way that made `output_tokens` a bad reward signal —
reasoning tokens billed separately and not included, say — then `step_tokens` would be
measuring less than it appears to and the reward would need revisiting on its own terms,
independently of this note.
