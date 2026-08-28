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
    return {
        "chat": chat_models,
        "response": response_models,
        "transcribe": transcribe_models,
        "embedding": embedding_models,
    }


def llm_hub_models(*, max_tokens, default_temperature, default_timeout):
    """LLM Hub catalog — a relay that speaks the OpenAI-compatible API.

    Deliberately tiny. The relay serves 77 of its 79 models under **bare** ids, while
    the openrouter catalog uses OpenRouter's own `vendor/model` naming; pointing one
    catalog at both would mean every entry's id depending on which base URL happened to
    be configured. A separate provider keeps each catalog true to one endpoint.

    Only the two models actually exercised here are registered. Adding an entry means
    checking that the relay serves it under that id — it answers an unknown one with
    "没有可用渠道服务模型", not with a fallback.
    """
    chat_models = [
        {
            "model_name": "llm_hub/claude-opus-5",
            "model_id": "claude-opus-5",
            "model_type": "chat/completions",
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
            # Reasoning must be switched on explicitly, and only this shape works: the relay
            # rejects `thinking.type.enabled` ("Use thinking.type.adaptive and
            # output_config.effort") and silently ignores an OpenAI-style `reasoning_effort`
            # (HTTP 200, no thinking). Verified live against the relay: adaptive + effort in
            # {low,medium,high,xhigh} are all accepted and return `reasoning_content`.
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
            # ChatLLMHub forwards this dict verbatim as `extra_body` (see _build_params).
            "reasoning": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}},
            # No `temperature`: Opus 4.7 and later removed the sampling parameters, and
            # the relay answers a request carrying one with "`temperature` is deprecated
            # for this model".
            "max_completion_tokens": max_tokens,
            "timeout": default_timeout,
        },
        {
            "model_name": "llm_hub/gpt-5.6-luna",
            "model_id": "gpt-5.6-luna",
            "model_type": "chat/completions",
            # A sibling of gpt-5.6-sol that, unlike sol, DOES accept function tools on
            # chat/completions (verified live: a tool prompt returns finish_reason
            # "tool_calls"), so the agent loop can use it directly here rather than on the
            # Responses surface. Registered as an escape hatch from the Bedrock-routed
            # `claude-opus-5`: that route currently returns empty completions for
            # "analyse/run a compiled binary" content — the whole ProgramBench task class —
            # while this GPT route (OpenAI-backed, not Bedrock) answers it normally
            # (verified: cmatrix/zip reverse-engineering prompts return real content).
            # sol itself is unusable through the relay right now (its upstream OpenAI account
            # returns 401 account_deactivated). No `temperature`: the gpt-5.x reasoning
            # models reject sampling params like the newer Anthropic ones.
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
            "model_name": "llm_hub/gpt-5.6-sol",
            "model_id": "gpt-5.6-sol",
            "model_type": "responses",
            # OpenAI's published spec. Codex reports 272k for its own bundle — that is a
            # billing threshold (input above it is priced 2x), not a capacity limit.
            "context_window": 1_050_000,
            "reasoning": {"effort": "low"},
            "max_output_tokens": max_tokens,
            "timeout": default_timeout,
        },
    ]
    return {"chat": chat_models, "response": response_models}


def anthropic_models(*, max_tokens, default_temperature, default_timeout, default_plugins, default_reasoning):
    """anthropic catalog.

    Register only models that support native structured output (the beta
    output_format parameter); callers request structured output on these.
    """
    chat_models = [
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
    return {"chat": chat_models}


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
    return {"chat": chat_models}


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
    return {"chat": chat_models}
