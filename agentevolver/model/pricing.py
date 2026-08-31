"""Central token-price catalog used by every model provider.

Prices are USD per *single* token.  Source tables normally quote one million
tokens, so :func:`per_million` performs that conversion once.  Runtime model
initialisation must stay offline and deterministic; prices are therefore
snapshotted here rather than fetched on every process start.

The catalog was refreshed on 2026-08-31 from:

* OpenAI standard API pricing: https://developers.openai.com/api/docs/pricing
* Anthropic API pricing: https://platform.claude.com/docs/en/about-claude/pricing
* Google Gemini paid-tier pricing: https://ai.google.dev/gemini-api/docs/pricing
* OpenRouter model metadata: https://openrouter.ai/api/v1/models
* LLM Hub effective pricing: ``GET /v1/me/pricing`` (authenticated)

LLM Hub can route one public model name through differently discounted
channels.  Its entries below use the upstream/official channel price so the
estimate is stable and comparable; the provider's final invoice may be lower.
OpenRouter normally reports an authoritative cost with each response, which
always overrides this table.  OpenRouter entries are fallback estimates for a
response that omitted that field.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable


PRICING_AS_OF = "2026-08-31"


def per_million(
    input_price: float,
    output_price: float,
    cache_read: float = 0.0,
    cache_write: float = 0.0,
    *,
    long_context_threshold: int | None = None,
    long_input: float | None = None,
    long_output: float | None = None,
    long_cache_read: float | None = None,
    long_cache_write: float | None = None,
) -> Dict[str, Any]:
    """Build the runtime per-token price mapping from per-million prices."""
    price: Dict[str, Any] = {
        "input": input_price / 1_000_000,
        "output": output_price / 1_000_000,
        "cache_read": cache_read / 1_000_000,
        "cache_write": cache_write / 1_000_000,
    }
    if long_context_threshold is not None:
        price["long_context_threshold"] = int(long_context_threshold)
        price["long_context"] = {
            "input": (long_input if long_input is not None else input_price) / 1_000_000,
            "output": (long_output if long_output is not None else output_price) / 1_000_000,
            "cache_read": (
                long_cache_read if long_cache_read is not None else cache_read
            ) / 1_000_000,
            "cache_write": (
                long_cache_write if long_cache_write is not None else cache_write
            ) / 1_000_000,
        }
    return price


# Direct providers and the relay use public/official, standard, short-context rates.
DIRECT_MODEL_PRICING: Dict[str, Dict[str, Any]] = {
    # OpenAI
    "openai/gpt-4o": per_million(2.50, 10.00, 1.25),
    "openai/gpt-4.1": per_million(2.00, 8.00, 0.50),
    "openai/gpt-5.6-sol": per_million(
        4.00, 20.00, 0.40, 5.00,
        long_context_threshold=272_000,
        long_input=8.00, long_output=30.00, long_cache_read=0.80,
        long_cache_write=10.00,
    ),
    "openai/gpt-5": per_million(1.25, 10.00, 0.125),
    "openai/gpt-5.1": per_million(1.25, 10.00, 0.125),
    "openai/o3": per_million(2.00, 8.00, 0.50),
    "openai/o3-mini": per_million(1.10, 4.40, 0.55),
    "openai/gpt-5.2": per_million(1.75, 14.00, 0.175),
    # No standalone gpt-5.3 row is published.  This legacy alias is estimated
    # from the exact official gpt-5.3-codex rate rather than left unpriced.
    "openai/gpt-5.3": per_million(1.75, 14.00, 0.175),
    "openai/gpt-5.4": per_million(
        2.50, 15.00, 0.25,
        long_context_threshold=272_000,
        long_input=5.00, long_output=22.50, long_cache_read=0.50,
    ),
    "openai/gpt-5.4-pro": per_million(
        30.00, 180.00,
        long_context_threshold=272_000,
        long_input=60.00, long_output=270.00,
    ),
    "openai/gpt-4o-transcribe": per_million(2.50, 10.00),
    "openai/text-embedding-3-small": per_million(0.02, 0.0),
    "openai/text-embedding-3-large": per_million(0.13, 0.0),
    "openai/text-embedding-ada-002": per_million(0.10, 0.0),

    # LLM Hub stable upstream/list-price estimates.  Hub channel discounts are
    # deliberately not baked in because routing can change between requests.
    "llm_hub/claude-opus-5": per_million(5.00, 25.00, 0.50, 6.25),
    "llm_hub/gpt-5.6-luna": per_million(
        0.20, 1.20, 0.02, 0.25,
        long_context_threshold=272_000,
        long_input=0.40, long_output=1.80, long_cache_read=0.04,
        long_cache_write=0.50,
    ),
    "llm_hub/deepseek-v4-flash": per_million(0.14, 0.28, 0.0028),
    "llm_hub/gpt-5.6-sol": per_million(
        4.00, 20.00, 0.40, 5.00,
        long_context_threshold=272_000,
        long_input=8.00, long_output=30.00, long_cache_read=0.80,
        long_cache_write=10.00,
    ),

    # Anthropic
    "anthropic/claude-opus-5": per_million(5.00, 25.00, 0.50, 6.25),
    "anthropic/claude-opus-4.8": per_million(5.00, 25.00, 0.50, 6.25),
    "anthropic/claude-fable-5": per_million(10.00, 50.00, 1.00, 12.50),
    "anthropic/claude-sonnet-4.5": per_million(3.00, 15.00, 0.30, 3.75),

    # Google, text rates.  Context-cache storage time is a separate Google
    # charge and cannot be inferred from token usage, so cache_write is zero.
    "google/gemini-2.5-flash": per_million(0.30, 2.50, 0.03),
    "google/gemini-2.5-pro": per_million(
        1.25, 10.00, 0.125,
        long_context_threshold=200_000,
        long_input=2.50, long_output=15.00, long_cache_read=0.25,
    ),
    # Retired preview kept for compatibility; the final 3.1 Pro standard rate
    # is the closest published successor and matches its historical tier.
    "google/gemini-3-pro-preview": per_million(
        2.00, 12.00, 0.20,
        long_context_threshold=200_000,
        long_input=4.00, long_output=18.00, long_cache_read=0.40,
    ),
    "google/gemini-3.1-pro-preview": per_million(
        2.00, 12.00, 0.20,
        long_context_threshold=200_000,
        long_input=4.00, long_output=18.00, long_cache_read=0.40,
    ),
    "google/gemini-3-flash-preview": per_million(0.50, 3.00, 0.05),
    # Retired preview; retained at its last OpenRouter-published token rate.
    "google/gemini-3.1-flash-lite-preview": per_million(0.25, 1.50, 0.025),
    "google/gemini-3.5-flash": per_million(1.50, 9.00, 0.15),
}


# OpenRouter prices are keyed by the upstream model id because plugin variants
# intentionally share one rate.  Values are a 2026-08-31 API snapshot.
OPENROUTER_MODEL_PRICING: Dict[str, Dict[str, Any]] = {
    "openai/gpt-4o": per_million(2.50, 10.00, 1.25),
    "openai/gpt-4.1": per_million(2.00, 8.00, 0.50),
    "openai/gpt-5": per_million(1.25, 10.00, 0.125),
    "openai/gpt-5.1": per_million(1.25, 10.00, 0.125),
    "openai/gpt-5.2": per_million(1.75, 14.00, 0.175),
    "openai/gpt-5.3": per_million(1.75, 14.00, 0.175),
    "openai/gpt-5.4": per_million(2.50, 15.00, 0.25),
    "openai/gpt-5.4-pro": per_million(30.00, 180.00),
    "openai/o3": per_million(2.00, 8.00, 0.50),
    "openai/o3-mini": per_million(1.10, 4.40, 0.55),
    "openai/gpt-5.3-codex": per_million(1.75, 14.00, 0.175),
    "anthropic/claude-3.5-sonnet": per_million(3.00, 15.00, 0.30, 3.75),
    "anthropic/claude-3.7-sonnet": per_million(3.00, 15.00, 0.30, 3.75),
    "anthropic/claude-sonnet-4": per_million(3.00, 15.00, 0.30, 3.75),
    "anthropic/claude-opus-4": per_million(15.00, 75.00, 1.50, 18.75),
    "anthropic/claude-sonnet-4.5": per_million(3.00, 15.00, 0.30, 3.75),
    "anthropic/claude-opus-4.5": per_million(5.00, 25.00, 0.50, 6.25),
    "anthropic/claude-sonnet-4.6": per_million(3.00, 15.00, 0.30, 3.75),
    "anthropic/claude-opus-4.6": per_million(5.00, 25.00, 0.50, 6.25),
    "anthropic/claude-opus-4.8": per_million(5.00, 25.00, 0.50, 6.25),
    "anthropic/claude-opus-5": per_million(5.00, 25.00, 0.50, 6.25),
    "anthropic/claude-fable-5": per_million(10.00, 50.00, 1.00, 12.50),
    "google/gemini-2.5-flash": per_million(0.30, 2.50, 0.03, 0.0833333333),
    "google/gemini-2.5-pro": per_million(1.25, 10.00, 0.125, 0.375),
    "google/gemini-3-pro-preview": per_million(2.00, 12.00, 0.20, 0.375),
    "google/gemini-3.1-pro-preview": per_million(2.00, 12.00, 0.20, 0.375),
    "google/gemini-3-flash-preview": per_million(0.50, 3.00, 0.05, 0.0833333333),
    "google/gemini-3.5-flash": per_million(1.50, 9.00, 0.15, 0.0833333333),
    "google/gemini-3.1-flash-lite-preview": per_million(0.25, 1.50, 0.025, 0.0833333333),
    "qwen/qwen3-coder": per_million(0.30, 1.00, 0.10),
    "qwen/qwen3-max": per_million(0.78, 3.90, 0.156, 0.975),
    "deepseek/deepseek-v3.2": per_million(0.269, 0.40, 0.1345),
    # Retired route retained at its last published OpenRouter rate.
    "x-ai/grok-4.1-fast": per_million(0.20, 0.50, 0.05),
}


def apply_pricing(catalog: Dict[str, Iterable[Dict[str, Any]]]) -> Dict[str, Iterable[Dict[str, Any]]]:
    """Attach a price to every model in a provider catalog, failing on drift."""
    missing = []
    for models in catalog.values():
        for model in models:
            name = model["model_name"]
            if name.startswith("openrouter/"):
                price = OPENROUTER_MODEL_PRICING.get(model["model_id"])
            else:
                price = DIRECT_MODEL_PRICING.get(name)
            if price is None:
                missing.append(name)
            else:
                model["cost"] = price
    if missing:
        raise ValueError(
            "Missing token pricing for registered model(s): " + ", ".join(sorted(missing))
        )
    return catalog

