---
status: implemented
date: 2026-08-15
owner: trace
affects:
  - agentevolver/trace/derive.py
  - tests/test_trace_derive.py
commits:
  - 47f51f9
---
# Agent Note: The projection appends the assistant turn before flushing its tool results

## Problem

`derive_messages` walks a session's log in write order and emits messages. The log's write
order is not the conversation's order: a step writes its tool events first and its
`AGENT_CALL` last, because the call event is written when the step **closes**.

Read naively — flush the pending tool results, then append the assistant turn — the
projection yields `[user, tool, tool, assistant]`. Providers reject that outright: "each
`tool_result` block must have a corresponding `tool_use` block in the previous message."
Every step after the first failed.

What kept it alive was the test. The fixture built its events in the opposite order from a
real log, so the code and the fixture agreed with each other and neither agreed with
`log/trace/*.jsonl`. Two artefacts confirming one another is not evidence.

## Decision

In the `AGENT_CALL` branch, the assistant message is appended **first** and `flush_results()`
is called after it. When that branch runs, `pending` holds the results of the step now
closing — not the previous step's — so the emitted order is
`[user, assistant(+tool_calls), tool, tool, ...]`, which is the shape a provider accepts.

The assistant turn carries its own calls, joined in from the log-only `TOOL_START` /
`SKILL_START` events keyed by step number. A call is part of the assistant's turn, not a
message of its own, so those events never join the surface and are collected separately.

The correction is stated in a comment at the branch itself, because the ordering looks like a
bug to anyone who reads the log's write order and assumes it matches the conversation.

## What this rules out

**Sorting the log before projecting.** There is no key to sort on that fixes this: the events
are in write order, and write order is correct for the log's purpose. The projection has to
know the convention, not fight it.

**Writing `AGENT_CALL` at step open instead of step close.** That would make write order match
conversation order, and it would also mean the event could not carry what the step decided —
the reasoning, the token usage, the calls — because none of it exists yet.

**Testing the projection against a hand-built fixture alone.** This is the failure that hid
the defect. `tests/test_trace_derive.py` now builds its events in the order a real log writes
them; a fixture that agrees with the code and disagrees with `log/trace/*.jsonl` proves
nothing.

## What would make this wrong

If any producer ever emits `AGENT_CALL` before its step's tool events, `pending` at that point
holds the *previous* step's results and they attach to the wrong assistant turn. Nothing
enforces the write order across producers — it is a convention of the hook that writes these
events, not a checked invariant.

If a provider appeared that accepted results before their calls, this would stop being a
correctness constraint and become a preference. None does today.
