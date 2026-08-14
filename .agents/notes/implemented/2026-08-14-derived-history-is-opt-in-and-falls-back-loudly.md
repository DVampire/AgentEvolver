---
status: implemented
date: 2026-08-14
owner: agent
affects:
  - agentevolver/agent/types.py
  - agentevolver/trace/derive.py
  - tests/test_derive_context_switch.py
commits:
  - 8523cb5
---
# Agent Note: Derived history is opt-in per agent, and every failure falls back to the rendered path

## Problem

`derive_context` replaces the model's history: instead of a prose transcript rebuilt from
memory on every step, the model sees the turns that actually happened — assistant messages
carrying their tool calls, tool messages carrying the results — projected from the session
log. It is the shape the model was trained on, and it appends rather than being rewritten, so
the prompt prefix can be cached.

It also changes what every step of every agent sees, which is not a change to make on an
argument alone. And the projection can fail in ways the caller cannot anticipate: the log may
not be retained by this process, the surface fold may refuse it, or it may simply be empty.

The failure that matters is the quiet one. A projection that returns a short history returns
something a provider will happily accept, and the model then acts on a conversation that
silently lost its earlier turns. Wrong answers with no error.

## Decision

Off by default, switched on per agent against a measurement.

`Agent._derived_messages` falls back to `rendered` in three cases, and logs a warning in the
two that indicate a problem:

- `trace_manager.events(session_id)` returns nothing — the log is not retained by this
  process. Warned.
- `derive_messages` raises `SurfaceError` — the log's surface declarations do not hold up.
  Warned, with the error.
- The projection is empty. Returns rendered without a warning; there was nothing to project.

Refusing beats projecting a history the log does not support, and falling back to the rendered
path is a working prompt, not a degraded one.

The measurement that justified turning it on for any agent at all, on `reverse_string`,
`code_agent`, five steps each way with `meta_agent` left rendered in the same run as a
control: rendered 19.5% prefix reuse, derived 99.0%, control 30.4%. A later, valid measurement
after the projection was fixed: rendered 53.3% cache hit, derived 78.1%.

## What this rules out

**Switching it on globally.** The claim is per-agent because the benefit is per-agent: an
agent whose history is short gains little, and an orchestrator's history is dominated by
sub-agent results.

**Failing the step when the projection fails.** The rendered path works. Turning an internal
projection problem into a failed run trades a small loss for a total one.

**Falling back silently.** The two warned cases are conditions someone needs to know about —
a run measured as "derived" that quietly ran rendered is a corrupted measurement, and this
repo has already produced one of those.

**Replacing the rendered turn wholesale.** Tried first. It scored 100% prefix reuse by
dropping everything the rendered turn carries besides history — the budget, step guidance,
todo list, workspace snapshot, and `errors`, which is where the repeat reminder rides.
Turning one feature on quietly disabled another. Those pieces are now re-attached as a
trailing volatile turn by `_split_rendered_turn`.

## What would make this wrong

The measurements are single runs on one task with two agents. They are consistent in
direction and large in size, but a task whose history is dominated by very long tool results
could plausibly reverse the arithmetic — the derived path repeats every result verbatim,
where the rendered path summarizes.

If the projection ever became reliable enough to be the default, the fallback becomes the
liability rather than the safety net: a silent fallback in a system that assumes projection
would make the caching measurements meaningless. The switch and the fallback stand or fall
together.
