---
name: model_openrouter
description: "Implements OpenRouter chat, responses, and REST integration plus provider-compatible serialization. Provider quirks remain localized here so Agents continue using the neutral Model API."
version: 1.0.0
type: provider
category: model
requirements: []
metadata: {}
---
# OpenRouter model provider

Implements OpenRouter chat, responses, and REST integration plus provider-compatible
serialization. Provider quirks remain localized here so Agents continue using the neutral
Model API.

| Path | Surface |
|---|---|
| `chat.py` | `/v1/chat/completions` — the default; streams token by token |
| `response.py` | `/v1/responses` — for models that refuse tools on chat |
| `rest.py` | Direct REST client used where the SDK does not fit |
| `serializer.py` | Message and tool conversion shared by the above |

## Why two surfaces

Some reasoning models refuse function tools on chat/completions:

```
Function tools with reasoning_effort are not supported for gpt-5.6-sol in
/v1/chat/completions. To use function tools, use /v1/responses or set
reasoning_effort to 'none'.
```

Every agent loop *is* tool calling, so "set reasoning to none" gives up the reason for
choosing such a model. The catalog routes those models to `response.py` instead, via a
`response` group and `model_type: "responses"`.

`response.py` deliberately has no `stream()`: `ModelContextManager.stream` already
buffers a client that lacks one and replays it as canonical events, and an agent step
consumes the whole turn before acting either way — a second wire format would be
maintenance without behaviour.

The two APIs disagree about what a turn is. Chat attaches tool calls to the assistant
message; Responses makes the call and its result separate items. `tool_call_id` is the
hinge and carries the same value on both sides, so the rest of the framework does not
have to know which surface answered. Note the id to echo is `call_id`, not the output
item's own `id`.
