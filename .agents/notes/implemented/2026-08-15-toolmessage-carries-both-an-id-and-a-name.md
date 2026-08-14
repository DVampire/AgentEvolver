---
status: implemented
date: 2026-08-15
owner: message
affects:
  - agentevolver/message/types.py
  - agentevolver/model/openai/serializer.py
  - agentevolver/model/anthropic/serializer.py
  - agentevolver/model/google/serializer.py
  - agentevolver/model/llm_hub/serializer.py
  - agentevolver/model/openrouter/serializer.py
  - tests/test_serializer.py
commits:
  - 47f51f9
---
# Agent Note: `ToolMessage` carries a call id and a tool name, because providers pair results to calls differently

## Problem

`AssistantMessage` has always carried `tool_calls`, but nothing carried what came back. A
transcript had to describe results in prose instead of replaying them, which is why the
rendered path folds every result into one user message.

Adding a real result message means each provider has to be told how to write it, and they do
not agree on the shape *or* on how a result is matched to its call:

- chat completions: a message with role `tool`, matched by `tool_call_id`.
- Responses API: a `function_call_output` item.
- Anthropic: a `tool_result` block on a **user** turn.
- Gemini: a `function_response` part on a user turn, matched by the declared function
  **name** — it never sees an id at all.

A representation that carries only an id cannot be replayed on Gemini. One that carries only a
name cannot disambiguate two calls to the same tool in one turn.

## Decision

`ToolMessage` carries both. `tool_call_id` matches the `ToolCall.id` the model issued;
`name` is the tool's declared name, and it exists specifically because Gemini pairs by it.
`is_error` is a separate field rather than a prefix inside `content`, so a consumer can count
or style failures without parsing prose.

Six serializer classes across five provider packages each convert it to their own shape —
`openai` has two, one for chat completions and one for the Responses API.
`tests/test_serializer.py` discovers them from the code rather than listing them, so a
provider added later is covered by arriving; the
first draft of that check was itself wrong, asserting that a tool result keeps its call id —
which Gemini never sees.

## What this rules out

**Pairing results to calls by position.** It works only while both ends survive in order.
An id survives replay, a log read out of context, and a range folded away by compaction;
position survives none of those.

**Carrying an id only.** Gemini cannot use it.

**Carrying a name only.** Two calls to `bash_tool` in one turn become indistinguishable.

**Letting each serializer synthesize the missing field.** A serializer that invents a name
from the id, or an id from the name, is guessing at the boundary where it is least
recoverable — and it would need the assistant turn to do it, which it is not given.

**Folding results into prose, as the rendered path does.** That is what this exists to
replace. It is still the fallback, and is still correct; it is just not the shape the model
was trained on.

## What would make this wrong

A provider that pairs by neither id nor name — by content hash, or by ordinal within a
declared batch — would need a third field, and the pattern of adding one field per provider
convention does not scale past a few.

The `is_error` flag is currently written by `derive.py` from the logged result's `success`
and read by serializers that have somewhere to put it. If a provider needed the error to be
*inside* the content to behave correctly, keeping it beside would become the wrong call.
