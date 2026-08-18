---
name: model
description: "Provides the model registry, role-based selection, API-key pooling, and provider-neutral generation interface."
version: 1.0.0
type: module
category: model
requirements: []
metadata: {}
---
# Model

Provides the model registry, role-based selection, API-key pooling, and provider-neutral
generation interface.

| Path | Responsibility |
|---|---|
| `types.py` | Model configuration contracts |
| `config.py` | Model configuration helpers |
| `context.py` | Registry, selection, API-key lifecycle, retry/fallback, and request recording |
| `pressure.py` | Deterministic request-size accounting and old tool-result pruning |
| `server.py` | Stable `model_manager` facade |
| `anthropic/`, `google/`, `openai/`, `openrouter/` | Provider serializers and clients |

Provider packages adapt wire protocols; Agents consume only the shared Manager contract.

## Request provenance

`ModelContextManager` is the single place that knows both the requested model and the
route actually dispatched after retry or fallback. Immediately before a provider call it
builds a versioned `trace.RequestSnapshot`, emits a `model_request` event, and asks trace
to flush. Recording in Agent would miss fallback routes; recording independently in each
provider would duplicate redaction and inevitably drift.

Agents pass `trace_context` (`task_id`, `agent_name`, `step_number`) as manager metadata.
It is consumed before provider serialization and never sent on the wire. Callers outside
an agent may omit it; calls without a real session id are not turned into synthetic trace
sessions merely because `ModelContext.from_context()` creates an internal id.

The snapshot allowlists behavioural request parameters. API keys and arbitrary transport
kwargs are excluded, while endpoint identity is represented only by a fingerprint. See
the trace module README for the durable schema and privacy boundary.

`trace_integrity_profile` controls what happens at this boundary. `interactive` records
an `integrity_degraded` fact when possible and preserves inference availability.
`training` and `high_risk` require an active writer, a successful flush, and a Session
without any prior queue/write gap; otherwise `TraceIntegrityError` is raised before the
provider is called. They also require a caller-supplied Session id; the manager never
turns its private bookkeeping id into fabricated provenance. Retry and fallback
deliberately re-raise that exception because a different route cannot repair missing
evidence for the request that was about to run.

## Request pressure

Immediately before snapshot and dispatch, `prepare_messages()` measures canonical request
JSON. For an OpenAI model recognized by `tiktoken`, it uses that model's native encoding;
other routes use the documented, deterministic UTF-8/4 fallback. It reserves the
configured output budget and compares the remaining input against the selected model's
`context_window` (or the manager's 1M default).

That window is a *guess*, and the two ways of guessing wrong are not symmetric. Too low
invents a wall the provider does not have: the request never leaves, and history is folded
to get under a limit that was never there — which is what a 128k default did to a model
that accepts a million. Too high is a wall the provider states, and
`provider_rejected_for_length()` reads that statement back as the same
`ContextOverflowError` a local overflow raises, so the run folds history and rebuilds
instead of re-sending an identical request until its attempts are gone. Guess high; let
the provider correct the guess. A catalog entry that knows its real window declares
`context_window` and every registration site passes it through to `ModelConfig`.

Below 85% pressure, the message objects are unchanged. Above it, the manager works from
oldest to newest and shortens only `ToolMessage.content`, retaining a head, tail, original
character count, and a statement that the complete result remains in Trace. System, user,
assistant, tool-call identity, and recent small results are not rewritten. It targets 75%
and records both estimates, thresholds, affected indices, and any unresolved overflow in
the request snapshot. Retry sends the same prepared request; a fallback route recalculates
against that model's own context window.

Pressure metadata names the method and carries two distinct accuracy flags. A tiktoken
count is exact for the canonical JSON text but **not** for provider wire framing, so
`tokenizer_exact=true` and `provider_wire_exact=false`; post-call provider usage remains
authoritative for billing and training metrics. This is not semantic memory compaction.

Deployments can install a local or gateway-cached counter with
`register_request_token_estimator(provider=..., model=..., estimator=...)`. Exact model
registrations override a provider-wide `"*"` registration, duplicates fail visibly, and
the returned disposer removes only the exact instance it registered. This makes custom
tokenizers hot-swappable without letting an old plugin cleanup delete a newer replacement.

The Anthropic 0.117 and Google GenAI 2.12 SDKs available in the supported `agentos`
environment expose `count_tokens`, but both are service calls, not local tokenizer
functions. Calling them here would add an extra remote request, rate-limit surface, and
failure mode to every generation; Google's count interface also does not accept the full
generation envelope used for dispatch. They are therefore not registered as defaults or
labelled provider-wire exact. Post-call usage remains authoritative, while deployments
that already cache a gateway-side count can register it explicitly. Model-generated
summaries remain a future layer after deterministic pruning is insufficient.
