"""Per-provider model specifications.

Model catalogs live here as data, kept separate from ``ModelContextManager``
(``context.py``). Each ``<provider>_models(...)`` function takes the runtime
defaults it needs and returns the model dicts for that provider; the manager
reads these specs and performs registration (client build + one ``ModelConfig``
per entry). This keeps the (large, frequently-edited) model lists out of the
manager logic.

Each entry is a plain dict with keys like ``model_name`` / ``model_id`` /
``model_type`` / ``temperature`` / ``max_completion_tokens`` / ``max_output_tokens``
/ ``reasoning`` / ``plugins`` / ``fallback_model`` — exactly what the registration
loops in ``context.py`` consume via ``m.get(...)``.
"""
from typing import Any, Dict, List

from agentevolver.model.pricing import apply_pricing


def _r(enabled: bool = True) -> Dict[str, Any]:
    """OpenRouter-style reasoning toggle shared by several specs."""
    return {"reasoning": {"enabled": enabled}}


def openai_models(
    *,
    max_tokens: int,
    default_temperature: Any,
    default_reasoning: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """OpenAI catalog, grouped by API surface (chat / responses / transcribe / embeddings)."""
    chat_models = [
        {
            "model_name": "openai/gpt-4o",
            "model_id": "gpt-4o",
            "model_type": "chat/completions",
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openai/gpt-4.1",
        },
        {
            "model_name": "openai/gpt-4.1",
            "model_id": "gpt-4.1",
            "model_type": "chat/completions",
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openai/gpt-4o",
        },
    ]
    response_models = [
        {
            "model_name": "openai/gpt-5.6-sol",
            "model_id": "gpt-5.6-sol",
            "model_type": "responses",
            "reasoning": {"reasoning": {"effort": "low", "context": "all_turns"}},
            "max_output_tokens": max_tokens,
            "context_window": 1_050_000,
            "native_compaction": True,
            "persisted_reasoning": True,
            "native_programmatic_tool_calling": True,
            "native_multi_agent": True,
            "supports_functions": True,
            # The fallback must preserve the Agent's function-call contract. The legacy
            # ResponseOpenAI adapter used by older response entries is text-only, while
            # gpt-4.1's chat adapter keeps the same canonical tool loop.
            "fallback_model": "openai/gpt-4.1",
        },
        {
            "model_name": "openai/gpt-5",
            "model_id": "gpt-5",
            "model_type": "responses",
            "reasoning": default_reasoning,
            "max_output_tokens": max_tokens,
            "fallback_model": "openai/o3",
        },
        {
            "model_name": "openai/gpt-5.1",
            "model_id": "gpt-5.1",
            "model_type": "responses",
            "reasoning": default_reasoning,
            "max_output_tokens": max_tokens,
            "fallback_model": "openai/gpt-5",
        },
        {
            "model_name": "openai/o3",
            "model_id": "o3",
            "model_type": "responses",
            "reasoning": default_reasoning,
            "max_output_tokens": max_tokens,
            "fallback_model": "openai/gpt-5.1",
        },
        {
            "model_name": "openai/o3-mini",
            "model_id": "o3-mini",
            "model_type": "responses",
            "reasoning": default_reasoning,
            "max_output_tokens": max_tokens,
            "fallback_model": "openai/gpt-5.1",
        },
        {
            "model_name": "openai/gpt-5.2",
            "model_id": "gpt-5.1",
            "model_type": "responses",
            "reasoning": default_reasoning,
            "max_output_tokens": max_tokens,
            "fallback_model": "openai/gpt-5",
        },
        {
            "model_name": "openai/gpt-5.3",
            "model_id": "gpt-5.3",
            "model_type": "responses",
            "reasoning": default_reasoning,
            "max_output_tokens": max_tokens,
            "fallback_model": "openai/gpt-5",
        },
        {
            "model_name": "openai/gpt-5.4",
            "model_id": "gpt-5.4",
            "model_type": "responses",
            "reasoning": {"reasoning": {"effort": "high"}},
            "max_output_tokens": max_tokens,
            "fallback_model": "openai/gpt-5",
        },
        {
            "model_name": "openai/gpt-5.4-pro",
            "model_id": "gpt-5.4-pro",
            "model_type": "responses",
            "reasoning": {"reasoning": {"effort": "high"}},
            "max_output_tokens": max_tokens,
            "fallback_model": "openai/gpt-5.4",
        },
    ]
    transcribe_models = [
        {
            "model_name": "openai/gpt-4o-transcribe",
            "model_id": "gpt-4o-transcribe",
            "model_type": "transcriptions",
            "fallback_model": "openai/gpt-4o-transcribe",
        },
    ]
    embedding_models = [
        {
            "model_name": "openai/text-embedding-3-small",
            "model_id": "text-embedding-3-small",
            "model_type": "embeddings",
            "fallback_model": "openai/text-embedding-3-large",
        },
        {
            "model_name": "openai/text-embedding-3-large",
            "model_id": "text-embedding-3-large",
            "model_type": "embeddings",
            "fallback_model": "openai/text-embedding-3-large",
        },
        {
            "model_name": "openai/text-embedding-ada-002",
            "model_id": "text-embedding-ada-002",
            "model_type": "embeddings",
            "fallback_model": "openai/text-embedding-3-large",
        },
    ]
    return apply_pricing({
        "chat": chat_models,
        "response": response_models,
        "transcribe": transcribe_models,
        "embedding": embedding_models,
    })


def llm_hub_models(*, max_tokens, default_temperature, default_timeout):
    """LLM Hub catalog — a relay that speaks the OpenAI-compatible API.

    Deliberately tiny. The relay serves 77 of its 79 models under **bare** ids, while
    the openrouter catalog uses OpenRouter's own `vendor/model` naming; pointing one
    catalog at both would mean every entry's id depending on which base URL happened to
    be configured. A separate provider keeps each catalog true to one endpoint.

    Only models actually exercised here are registered. Adding an entry means
    checking that the relay serves it under that id — it answers an unknown one with
    "没有可用渠道服务模型", not with a fallback.
    """
    chat_models = [
        {
            "model_name": "llm_hub/claude-fable-5-1",
            "model_id": "claude-fable-5-1",
            # Verified relay Messages route; do not alias this to Fable 5 or silently
            # enable sibling models' beta features. Native compaction is not yet probed.
            "model_type": "anthropic/messages",
            "native_compaction": False,
            "persisted_reasoning": True,
            "context_window": 1_000_000,
            "reasoning": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
            "max_completion_tokens": min(max_tokens, 128_000),
            "timeout": default_timeout,
            "fallback_model": "llm_hub/claude-opus-5",
        },
        {
            "model_name": "llm_hub/claude-opus-5",
            "model_id": "claude-opus-5",
            # Live-probed on this relay: /v1/messages supports native tools, signed
            # Anthropic blocks, compact_20260112, and compaction-block continuation.
            "model_type": "anthropic/messages",
            "native_compaction": True,
            "persisted_reasoning": True,
            # The relay routes this to AWS Bedrock (response ids are `msg_bdrk_*`), which
            # intermittently returns an empty completion (finish_reason null, ~3 tokens) —
            # mostly isolated single misses that one retry clears. The default of 3 attempts
            # is enough for those, and an empty now retries on a short flat 0.5s wait rather
            # than the exponential backoff (see `_is_transient_empty`), so the isolated case
            # costs almost nothing. A longer bad window no retry budget can outlast anyway is
            # the relay's to fix, not something more attempts here would rescue — so the
            # route keeps the default 3 rather than an inflated ceiling.
            # Confirmed against Anthropic's model page: 1M is both the default and the
            # maximum; there is no smaller context variant of Opus 5.
            "context_window": 1_000_000,
            # Reasoning must be switched on explicitly. The relay rejects
            # `thinking.type.enabled` and silently ignores OpenAI-style
            # `reasoning_effort`; native Messages accepts adaptive thinking plus
            # `output_config.effort` (also verified in the tool-loop probe).
            # Without this the route ran at the model's default effort — the reference agent
            # that leads this benchmark runs Opus 5 at xhigh, and the same scaffold one
            # effort/generation down scores multiples lower, so effort is a first-order lever.
            # `high` is the default. Effort helps up to a point, but past it the extra
            # thinking eats throughput: on clog, medium→high climbed 84.6%→87.9%, but xhigh
            # *dropped* to 75.5% — deeper per-step reasoning meant fewer steps (159 vs 185)
            # and a less complete build. `high` is the sweet spot for these build-heavy tasks;
            # override to `xhigh` per run with
            # `--cfg-options model.reasoning='{...effort: xhigh...}'` only if a task is
            # reasoning-bound rather than build-bound.
            # ChatAnthropic forwards this dict verbatim on the native Messages surface.
            "reasoning": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
            # No `temperature`: Opus 4.7 and later removed the sampling parameters, and
            # the relay answers a request carrying one with "`temperature` is deprecated
            # for this model".
            "max_completion_tokens": max_tokens,
            "timeout": default_timeout,
        },
        {
            "model_name": "llm_hub/gemini-3.8-flash",
            "model_id": "gemini-3.8-flash",
            "model_type": "chat/completions",
            # Verified on LLM Hub: image input, tool calls, tool-result replay,
            # and streaming. Keep unverified native features disabled.
            "supports_vision": True,
            # Conservative local budget until the relay publishes context limits.
            "context_window": 128_000,
            "max_completion_tokens": min(max_tokens, 32_768),
            "timeout": default_timeout,
        },
        {
            "model_name": "llm_hub/deepseek-v4-flash-vision-exp",
            "model_id": "deepseek-v4-flash-vision-exp",
            "model_type": "chat/completions",
            # Separate visual route, not the text-only 0731 alias. Verified image +
            # tool-result replay; unverified native features retain portable fallbacks.
            "supports_vision": True,
            "persisted_reasoning": True,
            "reasoning": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            "context_window": 1_000_000,
            "max_completion_tokens": max_tokens,
            "timeout": default_timeout,
        },
        {
            "model_name": "llm_hub/deepseek-v4-flash",
            "model_id": "deepseek-v4-flash",
            "model_type": "chat/completions",
            # Verified live against the relay under this bare id: a chat/completions call
            # returns content directly, finish_reason "stop", reasoning_tokens 0 — so it
            # behaves as a plain chat model here, not a reasoning one, and needs no
            # reasoning routing. DeepSeek accepts sampling params, so temperature is sent.
            #
            # Text only. The relay refuses an image part outright — 400 "Model do not
            # support image input" — so an agent driving a vision environment on this
            # route fails on EVERY call, not on the ones that happen to carry a
            # screenshot. Declared here because the llm_hub branch otherwise marks every
            # route vision-capable, which is the claim that let this reach a run.
            "supports_vision": False,
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "timeout": default_timeout,
        },
    ]
    # gpt-5.6-sol refuses function tools on chat/completions ("use /v1/responses or set
    # reasoning_effort to 'none'"). An agent loop is tool calling, so giving up
    # reasoning is not the trade to make — it is routed to the other surface instead.
    response_models = [
        {
            "model_name": "llm_hub/gpt-6-astra",
            "model_id": "gpt-6-astra",
            "model_type": "responses",
            # Verified relay tools, compact + replay, configuration_update and hosted
            # program execution, 2026-09-05. Native multi-agent remains opt-in/unverified.
            "native_compaction": True,
            "native_configuration_updates": True,
            # Verified launch → pending continuation → original call_id result replay.
            "native_async_tools": True,
            "native_programmatic_tool_calling": True,
            "persisted_reasoning": True,
            "explicit_prompt_cache": True,
            "reasoning": {"effort": "high", "context": "all_turns"},
            "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"],
            "supports_sampling": False,
            "context_window": 1_050_000,
            "max_output_tokens": max_tokens,
            "timeout": default_timeout,
            "fallback_model": "llm_hub/gpt-5.6-sol",
        },
        {
            "model_name": "llm_hub/gpt-5.6-sol",
            "model_id": "gpt-5.6-sol",
            "model_type": "responses",
            # Live-probed against this exact relay route: /responses/compact returns a
            # replayable opaque compaction item. Other Responses routes are not assumed
            # to support it merely because they share a client implementation.
            "native_compaction": True,
            "persisted_reasoning": True,
            "explicit_prompt_cache": True,
            # Live probe on this exact relay accepted both allowed_callers and the
            # programmatic_tool_calling hosted tool. Multi-agent is intentionally absent:
            # the same relay returned 400 because it strips the required beta header.
            "native_programmatic_tool_calling": True,
            # OpenAI's published spec. Codex reports 272k for its own bundle — that is a
            # billing threshold (input above it is priced 2x), not a capacity limit.
            "context_window": 1_050_000,
            # Keep reasoning-capable demo roles on the framework-wide default.  This is
            # also the effort used by the SWE-bench MetaAgent unless a run explicitly
            # overrides it; role-specific model routing must not silently lower it.
            "reasoning": {"effort": "high", "context": "all_turns"},
            "max_output_tokens": max_tokens,
            "timeout": default_timeout,
        },
        {
            "model_name": "llm_hub/gpt-5.6-luna",
            "model_id": "gpt-5.6-luna",
            "model_type": "responses",
            # Here for the same reason as its sibling, discovered the same way. A tool
            # prompt on chat/completions does return `finish_reason: "tool_calls"` — but
            # only when the request carries no reasoning effort. An agent loop sends one,
            # and the relay then answers 400: "Function tools with reasoning_effort are
            # not supported for gpt-5.6-luna in /v1/chat/completions. To use function
            # tools, use /v1/responses or set reasoning_effort to 'none'." Dropping the
            # effort is not the trade to make for a role that has to reason about a UI,
            # so it moves to the surface that takes both.
            #
            # It is an OpenAI-backed route rather than a Bedrock one, which is why it is
            # kept: it answers content that `claude-opus-5` returns empty for, and it
            # gives the co-design panel a third model family that can still see images.
            #
            # NOT native_compaction. Moving surfaces does not move a capability: only
            # `sol` has been live-probed for `/responses/compact`, and assuming a sibling
            # supports it because it now shares a client is the exact mistake
            # `test_only_verified_llm_hub_routes_declare_native_compaction` exists to
            # catch — which it did. The portable text checkpoint covers this route until
            # someone probes it.
            "persisted_reasoning": True,
            "context_window": 1_050_000,
            "reasoning": {"effort": "high", "context": "all_turns"},
            "max_output_tokens": max_tokens,
            "timeout": default_timeout,
        },
    ]
    return apply_pricing({"chat": chat_models, "response": response_models})


def anthropic_models(*, max_tokens, default_temperature, default_timeout, default_plugins, default_reasoning):
    """anthropic catalog.

    Register only models that support native structured output (the beta
    output_format parameter); callers request structured output on these.
    """
    chat_models = [
        {
            "model_name": "anthropic/claude-opus-5",
            "model_id": "claude-opus-5",
            "model_type": "chat/completions",
            # Official Anthropic Messages support for this exact model. Sibling models
            # remain opt-in because a shared client class is not a capability guarantee.
            "native_compaction": True,
            "persisted_reasoning": True,
            "reasoning": {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
            },
            "max_completion_tokens": max_tokens,
            "context_window": 1_000_000,
            "fallback_model": "anthropic/claude-opus-4.8",
        },
        # opus-4.8 and fable-5 reject `temperature` ("deprecated for this model"),
        # so they omit it and the request is sent without the parameter.
        {
            "model_name": "anthropic/claude-opus-4.8",
            "model_id": "claude-opus-4-8",
            "model_type": "chat/completions",
            "max_completion_tokens": max_tokens,
            "fallback_model": "anthropic/claude-sonnet-4.5",
        },
        {
            "model_name": "anthropic/claude-fable-5",
            "model_id": "claude-fable-5",
            "model_type": "chat/completions",
            "max_completion_tokens": max_tokens,
            "fallback_model": "anthropic/claude-sonnet-4.5",
        },
        {
            "model_name": "anthropic/claude-sonnet-4.5",
            "model_id": "claude-sonnet-4-5-20250929",
            "model_type": "chat/completions",
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "anthropic/claude-sonnet-4.5",
        },
    ]
    return apply_pricing({"chat": chat_models})


def openrouter_models(*, max_tokens, default_temperature, default_timeout, default_plugins, default_reasoning):
    """openrouter catalog (restored from git HEAD)."""
    chat_models = [
        # ---- OpenAI (via OpenRouter) ----
        {
            "model_name": "openrouter/gpt-4o",
            "model_id": "openai/gpt-4o",
            "model_type": "chat/completions",
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gpt-4.1",
            "model_id": "openai/gpt-4.1",
            "model_type": "chat/completions",
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gpt-5",
            "model_id": "openai/gpt-5",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gpt-5.1",
            "model_id": "openai/gpt-5.1",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gpt-5.2",
            "model_id": "openai/gpt-5.2",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gpt-5.3",
            "model_id": "openai/gpt-5.3",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gpt-5.4",
            "model_id": "openai/gpt-5.4",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gpt-5.4-pro",
            "model_id": "openai/gpt-5.4-pro",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/o3",
            "model_id": "openai/o3",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": 1.0,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/o3-mini",
            "model_id": "openai/o3-mini",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": 1.0,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gpt-5.3-codex",
            "model_id": "openai/gpt-5.3-codex",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        # ---- Anthropic (via OpenRouter) ----
        {
            "model_name": "openrouter/claude-sonnet-3.5",
            "model_id": "anthropic/claude-3.5-sonnet",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/claude-sonnet-3.7",
            "model_id": "anthropic/claude-3.7-sonnet",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/claude-sonnet-4",
            "model_id": "anthropic/claude-sonnet-4",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/claude-opus-4",
            "model_id": "anthropic/claude-opus-4",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/claude-sonnet-4.5",
            "model_id": "anthropic/claude-sonnet-4.5",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/claude-opus-4.5",
            "model_id": "anthropic/claude-opus-4.5",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/claude-sonnet-4.6",
            "model_id": "anthropic/claude-sonnet-4.6",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/claude-opus-4.6",
            "model_id": "anthropic/claude-opus-4.6",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            # reasoning is capped at 8k thinking tokens so long chains of
            # thought can't consume the whole max_completion_tokens budget
            # and truncate the structured-output JSON body (which produced
            # "Unterminated string" parse failures). extra_body is passed
            # straight through to OpenRouter as the top-level `reasoning`.
            "model_name": "openrouter/claude-opus-4.8",
            "model_id": "anthropic/claude-opus-4.8",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": {"max_tokens": 8000}},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            # Same 8k thinking cap as 4.8, and for the same reason — a long
            # chain of thought eating the completion budget truncates the
            # structured-output JSON. Thinking is *on by default* on Opus 5
            # (unlike 4.8, where omitting the config meant no thinking), so the
            # cap matters more here, not less.
            "model_name": "openrouter/claude-opus-5",
            "model_id": "anthropic/claude-opus-5",
            "model_type": "chat/completions",
            "context_window": 1_000_000,
            "reasoning": {"reasoning": {"max_tokens": 8000}},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/claude-fable-5",
            "model_id": "anthropic/claude-fable-5",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        # ---- Google (via OpenRouter) ----
        {
            "model_name": "openrouter/gemini-2.5-flash",
            "model_id": "google/gemini-2.5-flash",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gemini-2.5-pro",
            "model_id": "google/gemini-2.5-pro",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gemini-3-pro-preview",
            "model_id": "google/gemini-3-pro-preview",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gemini-3.1-pro-preview",
            "model_id": "google/gemini-3.1-pro-preview",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gemini-3-flash-preview",
            "model_id": "google/gemini-3-flash-preview",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            # Fast, cheap flash used as the universal fallback for every
            # openrouter model above. Its own fallback points elsewhere to
            # avoid a self-loop.
            "model_name": "openrouter/gemini-3.5-flash",
            "model_id": "google/gemini-3.5-flash",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3-flash-preview",
        },
        {
            "model_name": "openrouter/gemini-2.5-flash-plugins",
            "model_id": "google/gemini-2.5-flash",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "plugins": default_plugins,
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gemini-3-flash-preview-plugins",
            "model_id": "google/gemini-3-flash-preview",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "plugins": default_plugins,
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gemini-3.1-flash-lite-preview",
            "model_id": "google/gemini-3.1-flash-lite-preview",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gemini-3.1-flash-lite-preview-plugins",
            "model_id": "google/gemini-3.1-flash-lite-preview",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "plugins": default_plugins,
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/gemini-3.1-pro-preview-plugins",
            "model_id": "google/gemini-3.1-pro-preview",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "plugins": default_plugins,
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        # ---- Qwen (via OpenRouter) ----
        {
            "model_name": "openrouter/qwen3-coder",
            "model_id": "qwen/qwen3-coder",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        {
            "model_name": "openrouter/qwen3-max",
            "model_id": "qwen/qwen3-max",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        # ---- DeepSeek (via OpenRouter) ----
        {
            "model_name": "openrouter/deepseek-v3.2",
            "model_id": "deepseek/deepseek-v3.2",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
        # ---- xAI (via OpenRouter) ----
        {
            "model_name": "openrouter/grok-4.1-fast",
            "model_id": "x-ai/grok-4.1-fast",
            "model_type": "chat/completions",
            "reasoning": {"reasoning": _r()},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
    ]
    return apply_pricing({"chat": chat_models})


def google_models(*, max_tokens, default_temperature, default_timeout, default_plugins, default_reasoning):
    """google catalog."""
    chat_models = [
        {
            "model_name": "google/gemini-2.5-flash",
            "model_id": "gemini-2.5-flash",
            "model_type": "chat/completions",
            "reasoning": {"thinking_config": {"thinking_budget": -1, "include_thoughts": True}},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3-flash-preview",
        },
        {
            "model_name": "google/gemini-2.5-pro",
            "model_id": "gemini-2.5-pro",
            "model_type": "chat/completions",
            "reasoning": {"thinking_config": {"thinking_budget": -1, "include_thoughts": True}},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3-flash-preview",
        },
        {
            "model_name": "google/gemini-3-pro-preview",
            "model_id": "gemini-3-pro-preview",
            "model_type": "chat/completions",
            "reasoning": {"thinking_config": {"thinking_budget": -1, "include_thoughts": True}},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3-flash-preview",
        },
        {
            "model_name": "google/gemini-3.1-pro-preview",
            "model_id": "gemini-3.1-pro-preview",
            "model_type": "chat/completions",
            "reasoning": {"thinking_config": {"thinking_budget": -1, "include_thoughts": True}},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3-flash-preview",
        },
        {
            "model_name": "google/gemini-3-flash-preview",
            "model_id": "gemini-3-flash-preview",
            "model_type": "chat/completions",
            "reasoning": {"thinking_config": {"thinking_budget": -1, "include_thoughts": True}},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3-flash-preview",
        },
        {
            "model_name": "google/gemini-3.1-flash-lite-preview",
            "model_id": "gemini-3.1-flash-lite-preview",
            "model_type": "chat/completions",
            "reasoning": {"thinking_config": {"thinking_budget": -1, "include_thoughts": True}},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3-flash-preview",
        },
        {
            "model_name": "google/gemini-3.5-flash",
            "model_id": "gemini-3.5-flash",
            "model_type": "chat/completions",
            "reasoning": {"thinking_config": {"thinking_budget": -1, "include_thoughts": True}},
            "temperature": default_temperature,
            "max_completion_tokens": max_tokens,
            "fallback_model": "openrouter/gemini-3.5-flash",
        },
    ]
    return apply_pricing({"chat": chat_models})
