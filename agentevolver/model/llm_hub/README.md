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
request with a missing-beta-header error even when the header is explicitly supplied,
so orchestration stays in MetaAgent. Which relay hop rejects it is not established.

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

`response.py` supports both buffered requests and native SSE streaming. Both paths retain
provider output items and share request serialization and capability downgrade handling.

The APIs disagree about what a turn is. Chat attaches calls to the assistant and returns
results through a `tool` role; Responses makes calls and results separate items; Anthropic
Messages represents calls as `tool_use` blocks and results as user-side `tool_result`
blocks. Provider serializers own those differences. `ContextEnvelope` validates the one
provider-neutral assistant/tool relationship before any of them sees it.

## GPT-6 and route fallbacks

`llm_hub/gpt-6-astra` uses Responses, with GPT-5.6 Sol as its configured fallback.
The relay's tool-call/result round-trip and native compaction/replay were verified on
2026-09-05. A subsequent PTC probe executed a hosted program, called a client-owned
arithmetic tool, replayed its caller-tagged result, and completed with `42`.
Native PTC is now eligible when requested, for tools explicitly marked programmatic.
Additional bounded probes on 2026-09-05:

| Feature | LLM Hub result | Framework behavior |
|---|---|---|
| Native async tools | Launch, continuation before result, and original `call_id` result replay succeeded | Opt-in `Agent.async_tool_calling=True` overlaps explicitly safe reads with generation |
| WebSocket steering | Responses WebSocket handshake returned HTTP 404 | Local mailbox at the next safe point; no native acknowledgement claimed |
| Native multi-agent | HTTP 400 missing beta header, with both SDK `betas` and explicit `OpenAI-Beta` | Local runtime agents remain authoritative |

The async implementation was also exercised through `ResponseLLMHub.stream` and the
actual `ActionExecutor`: one tool executed once, started 0.626 seconds before generation
ended, and its result replay produced `42` (316 tokens for the two requests). This is a
small correctness probe, not a benchmark speedup claim.

Async is a per-agent, provider-neutral opt-in; it defaults off to preserve the current
PTC batching/cost policy. A route without support or an explicit async rejection falls
back to awaited batches on the same model. Only the existing explicitly opted-in,
read-only tool roster is eligible. The executor checks it again, waits at the first
effectful-call barrier, and never executes partial argument deltas. Browser actions and
workspace writes stay sequential. A completed call's `caller` linkage survives the
Agent boundary as well as the serializer.

This is **within-turn overlap**, not arbitrary pending jobs across model turns: all jobs
are joined or cancelled before context is committed. Four-layer validation and native
compaction therefore still receive complete tool turns. No pending tool result is
fabricated. Stream failures are not replayed after output starts, and closing the model
stream closes its underlying transport and reaps read tasks. Closing a connection does
not prove that a provider immediately stopped computation or billing.

Snapshots distinguish native async from `awaited_tool_batches`, and continue to report
`mid_turn_steering=next_turn_mailbox`. Local delivery receipts distinguish queued,
received, delivered, failed, interrupted and undelivered messages; they do not mean a
remote model has acknowledged or applied a mid-turn instruction. Native multi-agent
also falls back when tools require local isolation or the input contains a compaction
checkpoint; the hosted beta cannot replace our independently scoped browser/user agents.

Protocol references: [async tools](https://developers.openai.com/api/docs/guides/async-tool-calling),
[steering](https://developers.openai.com/api/docs/guides/steering),
[multi-agent](https://developers.openai.com/api/docs/guides/responses-multi-agent).

The relay also accepted `configuration_update`. For a per-call `reasoning_effort`
override on a stateless single-agent request, Astra keeps its baseline request-level
effort and inserts the update before the latest user input. This preserves the fixed
prefix, not necessarily the entire rolling cache. Multi-agent/stateful continuations
and unsupported routes use ordinary request parameters instead. Claude uses its
`output_config.effort` equivalent. No per-session state is hidden in a shared client.

`provider_request_serialized` trace events link to the canonical request snapshot and
record a hash, byte count, input item types and tool count of the final SDK payload.
They do not duplicate image data or claim to capture SDK-generated HTTP headers.
Incomplete Responses preserve their diagnostic text but cannot execute partial tool
arguments or replace a checkpoint. Output-limit/content-filter/cancellation results
are not retried with the same budget. Portable summaries use a shorter target and low
reasoning effort, leaving more output budget for the actual checkpoint.

The Responses serializer preserves text, images and PDF inputs. Unsupported content
types raise an error instead of disappearing. Route configuration controls sampling and
reasoning limits; Astra maps `none`/`minimal` to `low` and omits sampling parameters.

On opted-in routes, stable input messages marked by the context assembler receive
OpenAI cache breakpoints. The last two tool results receive rolling boundaries, and
the implicit boundary remains enabled (at most four writes altogether).
This uses OpenAI's 30-minute TTL, not Anthropic's TTL or cache-control schema. Rejection
removes both breakpoints and options on retry. Cache reads and writes still determine
the actual savings; an accepted request is not proof of a cache hit.

Native optimizations are optional. The model manager records rejection per route with
a TTL and downgrades to ordinary calls, local orchestration, or portable checkpoints.
Each rejected feature unlocks at most one additional retry. Authentication errors,
rate limits and server errors do not establish unsupported capabilities.

Responses and Anthropic state record their originating model. A different model receives canonical
messages/tool results and the readable checkpoint rather than incompatible opaque
state. Source history is not mutated. An opaque-only checkpoint cannot safely switch
models and is rejected explicitly. The readable companion summary is therefore kept
for portability. Both native and text compaction receive the prior checkpoint; the new
summary replaces it rather than appending it again.
