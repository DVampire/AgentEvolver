---
name: model_llm_hub
description: "Implements LLM Hub chat, Anthropic Messages, and Responses surfaces under one credential pool without colliding with OpenRouter model names."
version: 1.0.0
type: provider
category: model
requirements: []
metadata: {}
---
# LLM Hub model provider

Implements the LLM Hub relay as its own provider, with chat, Anthropic Messages, and
Responses surfaces, so its bare model ids never collide with OpenRouter naming.

| Path | Surface |
|---|---|
| `chat.py` | `/v1/chat/completions` — OpenAI-compatible chat models |
| `../anthropic/chat.py` | `/v1/messages` — Claude tools, cache, and native compaction |
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

The catalog is intentionally a checked subset rather than a mirror of every upstream
model. Adding one means verifying both its bare id and its actual protocol surface, since
the relay neither rewrites unknown ids nor guarantees that every model supports every API.

Native compaction is separately opt-in through the model spec's
`native_compaction=True`. Sharing the Responses or Anthropic Messages client does not opt a
model in: the exact relay/model route must have completed a generate-and-replay probe.
Unmarked chat models use the same portable text checkpoint as every other provider.

The Responses adapter keeps every returned output item in order. That one opaque list is
the continuation state for encrypted reasoning, `program` / `program_output`, function
call caller linkage, and beta multi-agent items; reconstructing only the visible text
would corrupt all four. On this relay `gpt-5.6-sol` has live-probed compaction and
programmatic tool calling. Multi-agent remains disabled because the relay rejects the
request after dropping its required beta header, so orchestration stays in MetaAgent.

`claude-opus-5` omits `temperature`: Opus 4.7 and later removed the sampling parameters,
and a request carrying one comes back "`temperature` is deprecated for this model". It is
routed through the relay's native Anthropic Messages surface, which was live-probed for
tool use and a complete `compact_20260112` generate/replay cycle. The public model name and
credential pool remain `llm_hub`; only the provider protocol adapter changes.

## Why three surfaces

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

The APIs disagree about what a turn is. Chat attaches calls to the assistant and returns
results through a `tool` role; Responses makes calls and results separate items; Anthropic
Messages represents calls as `tool_use` blocks and results as user-side `tool_result`
blocks. Provider serializers own those differences. `ContextEnvelope` validates the one
provider-neutral assistant/tool relationship before any of them sees it.
