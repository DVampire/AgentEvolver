---
status: implemented
date: 2026-08-14
owner: prompt
affects:
  - agentevolver/prompt
  - agentevolver/model/llm_hub/serializer.py
  - agentevolver/model/anthropic/serializer.py
  - agentevolver/model/openrouter/serializer.py
  - agentevolver/agent/types.py
commits:
  - 95c4d63
  - 2992d7f
---
# Agent Note: The capability catalogs go ahead of the agent state, and the breakpoint goes between them

## Problem

The capability catalogs are byte-identical on every step of a session and were 63% of the
prompt in a measured run. Every one of those characters was being read at full price, for two
independent reasons that each made the other invisible:

- The only `cache_control` breakpoint sat on the system message, and the catalogs are in the
  user turn — so they fell after the last cacheable byte.
- The templates rendered the catalogs *after* `<agent-context>`. A cache keeps a prefix, and
  the step counter in the agent state changes every step, so everything behind it was
  invalidated regardless of where the breakpoint went.

Fixing either one alone changes nothing, which is why neither was noticed.

## Decision

Two changes that only work together.

**Layout.** The four capability blocks merge into one `<capability-context>` container across
27 templates, placed *ahead* of `<agent-context>`. The ordering rule is not "where did this
text come from" but "does it change between steps": stable content first, volatile content
last. `Agent._split_rendered_turn` applies the same rule when it partitions a rendered turn
for the projected path.

**Breakpoint.** A second `cache_control` breakpoint lands at the end of
`</capability-context>` in the `llm_hub`, `openrouter` and `anthropic` serializers.
`_cache_split` returns `None` for a turn that carries no catalog, and such a turn gets no
breakpoint of its own — a breakpoint placed after content that changes every step caches
nothing and spends a write to learn it.

Measured on `claude-opus-5` through the relay, one step: `cache_read` went from 0 to 7,383 of
7,405 input tokens (99.7%). Measured across a `reverse_string` run with `meta_agent`, the
first call reads nothing and the next two read 72,647 tokens each — 73.5% and 72.5%. Before
the reorder that column was zero throughout, and the reorder alone lifted the rendered path's
prefix reuse from 30.5% to 98.7%, with `derive_context` still switched off.

## What this rules out

**Ordering the prompt by origin.** "It came from the per-step render" is not the same question
as "does it change per step". Sending the catalogs after the history put 61,000 unchanging
characters beyond the last reusable byte and scored 20% prefix reuse — no better than not
projecting at all.

**One breakpoint on the system message.** It caches the system prompt, which is the smaller
half, and nothing in the user turn.

**A breakpoint per user turn.** Turns without a catalog must not get one; the write buys
nothing back.

**Putting anything volatile inside `<capability-context>`.** Anything placed there costs the
whole prefix on every step. This is the constraint that the
[catalog freeze](2026-08-14-freeze-the-capability-catalog-for-the-session.md) then defends,
because the catalog itself becomes volatile the moment evolution registers a component.

## What would make this wrong

The layout is worth its cost only while the catalogs dominate the prompt. If they shrank —
fewer tools, shorter descriptions, per-agent catalogs — the 63% figure that motivates this
would not hold, and the breakpoint might be better spent elsewhere.

It is also provider-shaped. Gemini caches implicitly with no breakpoint to set, so it gets
none of the breakpoint half of this decision and all of the layout half. A provider that
cached suffixes, or that keyed on content, would want a different arrangement.

The measurements above are single runs on `reverse_string` and `penguins_analysis`. They are
large enough that noise is not a plausible explanation for 0% versus 99.7%, but they say
nothing about how the split behaves on a task with a long tool history.
