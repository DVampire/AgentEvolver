# Postmortem 0001: `derive_context` shipped broken, and the broken run was measured as a success

## Summary

`derive_context` — the switch that replaces an agent's rendered prose transcript with the
turns projected from the session log — was shipped, measured, tabulated and written up as a
working alternative. It had never worked. Turning it on produced a message history no
provider could serialize, so every step after the first failed; the run then reported success
anyway, because `agent_end` was hardcoded to `success=True`. The numbers taken from that log
were compared against the rendered path and presented as evidence that the projection helped.

Three defects were involved and each hid the next: no serializer knew the message type the
projection emits, the projection emitted turns in an order providers reject, and the outcome
recorded in the log ignored what actually happened. The last one is why the first two survived
being measured.

## What happened

`derive_context` landed off by default in commit `8523cb5`, with measurements: `reverse_string`,
`code_agent`, 19.5% prefix reuse rendered against 99.0% derived, with `meta_agent` left on the
rendered path in the same run as a control. Those numbers were used to justify the approach and
carried into a later comparison across tasks.

The path was broken in three places.

**No serializer knew `ToolMessage`.** The projection emits real turns —
`[user, assistant(+tool_calls), tool, ...]` — while the rendered path folds tool results into
prose inside a single user message. So `ToolMessage` reached a serializer for the first time
when the switch was turned on, and every one of the six raised `Unknown message type`. Each
provider needs a different shape: a `tool` role on chat completions, `function_call_output` on
the Responses API, a `tool_result` block on a user turn for Anthropic, a `function_response`
part for Gemini — which pairs a result to its call by declared function *name* rather than by
id, so the type had to grow a `name` field to be replayable there at all.

**The projection inverted every turn.** A step writes its tool events first and its
`AGENT_CALL` last, because the call event is written when the step closes. `derive.py` flushed
pending results *before* appending the assistant turn, producing
`[user, tool, tool, assistant]`. Anthropic rejects that outright: "each `tool_result` block
must have a corresponding `tool_use` block in the previous message."

**`agent_end` hardcoded `success=True`.** The trace hook built its `agent_end` event with a
constant success and no result, so every session in the log claimed to have finished
successfully — including a run that gave up after three consecutive model errors.

Put together: the switch was turned on, every step after the first failed to serialize, the
run gave up, the log recorded a successful session, and a measurement script read that log
and produced a cache-hit rate for it. The rate was real — a run that fails fast reads a small,
stable prompt — and it was measuring nothing anyone wanted to know.

The fix landed in commit `47f51f9`. The first valid measurement of the switch, `reverse_string`,
five steps each way, no errors and no unreported usage: rendered 53.3% cache hit, derived
78.1%. Still a win, and a much smaller one than the number that had been published.

## Why nothing caught it

**The test fixture and the code were built from the same wrong assumption.** `test_trace_derive.py`
constructed its events in the opposite order from a real log — assistant first, then tool
results. The code was written to satisfy the fixture. The two agreed with each other, both
tests passed, and neither agreed with `log/trace/*.jsonl`. Two artefacts confirming one
another is not evidence, and there was nothing in the repo that read a real log and compared.

**A message type could reach a serializer for the first time with nothing to notice.** Each
serializer was individually correct for every message type it had ever been given. The defect
was that six of them had to learn one new type and none had — a class of failure no unit test
can see, because every unit is right. There was no check that walked the message subclasses
against the serializers.

**The success flag was a constant, so the log could not contradict the measurement.** This is
the one that made the other two survive. A measurement is only as honest as the record it
reads, and the record was incapable of reporting a failure. Every run looked like a data
point, so a run that failed on step two was pooled with runs that completed.

**The measurement was one task, one run.** Every conclusion about `derive_context` rested on a
single `reverse_string` run. There was no second task to disagree with it and no per-run
breakdown to make an anomalous one visible.

## What changed

- `ToolMessage` gained a `name` field and all six serializers learned the type. The decision
  is recorded in [the `ToolMessage` note](../../.agents/notes/implemented/2026-08-15-toolmessage-carries-both-an-id-and-a-name.md).
- `derive.py` appends the assistant turn before flushing its results, and `test_trace_derive.py`
  builds its events in the order a real log writes them. See
  [the ordering note](../../.agents/notes/implemented/2026-08-15-the-assistant-turn-precedes-its-results.md).
- The trace hook reports `inp.get("success")` rather than a constant, and passes the run's
  result through.
- `tests/test_serializer.py` discovers every `Message` subclass and every provider serializer
  from the code and checks the cross-product, so a type added later is covered by arriving.
  `tests/test_registration.py` closes the gap that check cannot see — a provider package with
  no serializer module is invisible to a walk over serializers, so it must be named with a
  reason.
- `tests/test_consistency_checks_can_fail.py` reintroduces the real defects in a subprocess
  and requires the corresponding check to go red, because a check that cannot fail reports the
  invariant as held.
- `scripts/context_baseline.py` measures several tasks, reports each separately, pools totals
  per label rather than averaging per-run rates, and names tasks that produced no session
  instead of counting them.

The gap that remains: nothing checks a projection against a real trace file. The fixtures are
now built in the real write order, but they are still fixtures, and the failure above is
precisely what happens when a fixture and the code it tests drift together.
