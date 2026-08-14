---
name: model_llm_hub
description: "Implements the LLM Hub relay as its own provider, with chat and responses surfaces, so its bare model ids never collide with OpenRouter's vendor/model naming."
version: 1.0.0
type: provider
category: model
requirements: []
metadata: {}
---
# LLM Hub model provider

Implements the LLM Hub relay as its own provider, with chat and responses surfaces, so
its bare model ids never collide with OpenRouter's vendor/model naming.

| Path | Surface |
|---|---|
| `chat.py` | `/v1/chat/completions` — streams token by token |
| `response.py` | `/v1/responses` — for models that refuse tools on chat |
| `rest.py` | Direct REST client used where the SDK does not fit |
| `serializer.py` | Message and tool conversion shared by the above |

## Why a separate provider rather than a base-URL swap

The relay speaks the OpenAI-compatible API, so pointing `OPENROUTER_API_BASE` at it
almost works — and then does not: it serves 77 of its 79 models under **bare** ids
(`claude-opus-5`), while the openrouter catalog uses OpenRouter's own naming
(`anthropic/claude-opus-5`). An unknown id is refused outright ("没有可用渠道服务模型"),
not served by a fallback, so one catalog pointed at both endpoints would have every
entry's id depend on which base URL happened to be configured.

Separate providers keep each catalog true to one endpoint. Credentials are
`LLM_HUB_API_BASE` / `LLM_HUB_API_KEY`; without them the provider registers nothing,
so a deployment that does not use the relay logs no failure.

## The catalog is deliberately tiny

Only the two models actually exercised here are registered — `claude-opus-5` (chat) and
`gpt-5.6-sol` (responses). Adding one means checking that the relay serves it under that
id, since it will not fall back.

`claude-opus-5` omits `temperature`: Opus 4.7 and later removed the sampling parameters,
and a request carrying one comes back "`temperature` is deprecated for this model". The
client's default is therefore `None` and the manager passes the catalog value through
as-is rather than substituting a default.

## Why two surfaces

`gpt-5.6-sol` refuses function tools on chat/completions:

```
Function tools with reasoning_effort are not supported for gpt-5.6-sol in
/v1/chat/completions. To use function tools, use /v1/responses or set
reasoning_effort to 'none'.
```

Every agent loop *is* tool calling, so turning reasoning off gives up the reason for
picking such a model; the catalog routes it to `response.py` instead.

`response.py` has no `stream()` on purpose: `ModelContextManager.stream` already buffers
a client that lacks one and replays it as canonical events, and an agent step consumes
the whole turn before acting either way.

The two APIs disagree about what a turn is — chat attaches tool calls to the assistant
message, responses makes the call and its result separate items. `tool_call_id` is the
hinge and carries the same value on both sides. The id to echo is `call_id`, not the
output item's own `id`.
