---
status: implemented
date: 2026-08-15
owner: model
affects:
  - agentevolver/model/llm_hub/serializer.py
  - agentevolver/model/anthropic/serializer.py
  - agentevolver/model/openrouter/serializer.py
commits:
  - ce727ae
---
# Agent Note: The cached prefix is written with a one-hour TTL, not the default five minutes

## Problem

The default cache TTL is five minutes. That is shorter than the gap between an
orchestrator's own steps, and shorter by construction, not by accident: a delegating agent
dispatches a sub-agent, the sub-agent runs for minutes, and by the time the orchestrator takes
its next step the cache entry has expired.

Measured on `penguins_analysis`, `meta_agent` wrote 308,469 input tokens across three steps
and read back **zero**, while agents in the same run whose steps are seconds apart hit 36–49%.
That pattern — one agent at zero while its siblings hit — is the signature of a TTL expiring,
not of an unstable prefix. An unstable prefix would have shown up everywhere.

## Decision

`CACHE_TTL = "1h"`, applied to every breakpoint a serializer writes: the system message and
the split at `</capability-context>`. Each of the three serializers that writes a breakpoint
— `llm_hub`, `anthropic`, `openrouter` — defines the constant itself, so the three copies
must agree. Anthropic had no `cache_control` at all before this landed, so nothing it sent
was ever cacheable.

The arithmetic is what decides it. A one-hour write costs 2x base against 1.25x for the
five-minute default; reads are 0.1x either way. So a single hit that would otherwise have
missed already pays for the more expensive write — and the case this fixes was missing
*every* hit, not some of them.

## What this rules out

**Leaving the default and accepting the misses.** The measured cost of doing so is 308,469
tokens re-read at full price in one three-step orchestration.

**Choosing the TTL per agent.** Tempting, because only delegating agents have step gaps that
long. It would need the serializer to know the agent's role, which it does not and should not:
the serializer's input is a message list. The uniform hour costs the fast agents 0.75x extra
on their writes and nothing on their reads.

**Reducing the gap instead.** The gap is the sub-agent doing its work. Shortening it means
shortening the delegated task.

## What would make this wrong

If a session's steps were reliably under five minutes apart — no delegation, or delegation
that returns fast — the extra 0.75x on every write is pure loss, and the cheaper TTL wins.
A run whose agents all sit at high hit rates on the five-minute default is the observation to
look for.

The 2x / 1.25x / 0.1x multipliers are provider pricing. If they move, redo the arithmetic
rather than trusting this note.

The value being defined three times is a standing hazard: a fourth serializer that grows a
breakpoint will define its own, and three copies drifting apart is the shape of defect this
repo has already been bitten by more than once. Nothing currently checks that the three
agree.
