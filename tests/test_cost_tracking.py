"""Per-call and per-task LLM cost recording. The relay (llm_hub/Bedrock) returns no cost in
usage, so a call is priced from the model's per-token list price; a provider that DOES return
a cost (OpenRouter) keeps its own number. These tests pin the fallback logic, that pricing
never double-counts cached tokens, and that a task's spend rolls up from its trace.
"""

from __future__ import annotations

import importlib.util
import json
import os

from agentevolver.model.types import compute_cost, price_usage_dict, TokenUsage
from agentevolver.model.config import llm_hub_models

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPUS_PRICE = {"input": 5e-6, "output": 2.5e-5, "cache_write": 6.25e-6, "cache_read": 5e-7}


def _load_launcher():
    spec = importlib.util.spec_from_file_location(
        "rpb_cost", os.path.join(ROOT, "examples", "run_programbench.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- pricing

def test_compute_cost_prices_each_token_class():
    cost = compute_cost(
        {"input_tokens": 1000, "output_tokens": 500,
         "cache_write_tokens": 200, "cache_read_tokens": 400},
        OPUS_PRICE,
    )
    # 1000*5e-6 + 500*2.5e-5 + 200*6.25e-6 + 400*5e-7
    assert cost == 1000 * 5e-6 + 500 * 2.5e-5 + 200 * 6.25e-6 + 400 * 5e-7


def test_cache_prices_default_to_multiples_of_input():
    cost = compute_cost(
        {"input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 1000, "cache_read_tokens": 1000},
        {"input": 1e-5, "output": 1e-5},  # no cache prices → write 1.25x, read 0.1x
    )
    assert cost == 1000 * (1e-5 * 1.25) + 1000 * (1e-5 * 0.1)


def test_no_pricing_means_no_cost():
    assert compute_cost({"input_tokens": 10}, None) is None
    assert compute_cost({"input_tokens": 10}, {}) is None


def test_provider_cost_is_kept_over_the_estimate():
    # OpenRouter-style: a top-level cost in the raw usage wins, no estimate applied.
    priced = price_usage_dict({"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.99}, OPUS_PRICE)
    assert priced["cost"] == 0.99


def test_missing_cost_is_estimated_from_price():
    priced = price_usage_dict({"prompt_tokens": 1000, "completion_tokens": 500}, OPUS_PRICE)
    assert priced["input_tokens"] == 1000 and priced["output_tokens"] == 500
    assert priced["cost"] == 1000 * 5e-6 + 500 * 2.5e-5


def test_cached_tokens_are_not_billed_twice_as_input():
    # from_raw puts cache_read in its own bucket, NOT in input_tokens — so pricing a response
    # with a big cache read must not also charge those tokens at the full input rate.
    priced = price_usage_dict(
        {"prompt_tokens": 100, "completion_tokens": 0, "cache_read_input_tokens": 10000},
        OPUS_PRICE,
    )
    assert priced["cache_read_tokens"] == 10000
    assert priced["cost"] == 100 * 5e-6 + 10000 * 5e-7  # input at full, cache read at 0.1x


def test_openai_total_input_is_split_from_its_cached_subset():
    priced = price_usage_dict(
        {
            "prompt_tokens": 10_000,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 8_000},
        },
        OPUS_PRICE,
    )
    assert priced["context_input_tokens"] == 10_000
    assert priced["input_tokens"] == 2_000
    assert priced["cache_read_tokens"] == 8_000
    assert priced["cost"] == 2_000 * 5e-6 + 8_000 * 5e-7 + 50 * 2.5e-5


def test_anthropic_uncached_input_is_added_to_cache_for_context_size():
    usage = TokenUsage.from_raw({
        "input_tokens": 2_000,
        "output_tokens": 50,
        "cache_read_input_tokens": 8_000,
    })
    assert usage.input_tokens == 2_000
    assert usage.context_input_tokens == 10_000
    assert usage.total == 10_050


def test_normalising_canonical_usage_is_idempotent():
    once = TokenUsage.from_raw({
        "prompt_tokens": 10_000,
        "prompt_tokens_details": {"cached_tokens": 8_000},
    })
    twice = TokenUsage.from_raw(once.model_dump())
    assert twice == once


def test_opus_catalog_carries_the_price_table():
    opus = next(m for m in llm_hub_models(max_tokens=8000, default_temperature=None, default_timeout=600)["chat"]
                if m["model_id"] == "claude-opus-5")
    assert opus.get("cost") == OPUS_PRICE


# --------------------------------------------------------------------------- per-task rollup

def _write_trace(tmp_path, lines):
    trace_dir = tmp_path / "log" / "trace"
    trace_dir.mkdir(parents=True)
    with open(trace_dir / "inst.jsonl", "w", encoding="utf-8") as h:
        for obj in lines:
            h.write(json.dumps(obj) + "\n")
    return str(tmp_path)


def test_summarise_spend_rolls_up_the_trace(tmp_path):
    launcher = _load_launcher()
    root = _write_trace(tmp_path, [
        {"event_type": "agent_call", "duration_ms": 1200,
         "usage": {"input_tokens": 1000, "output_tokens": 500, "cost": 0.0175}},
        {"event_type": "agent_call", "duration_ms": 800,
         "usage": {"input_tokens": 2000, "output_tokens": 100, "cost": 0.0125}},
        {"event_type": "agent_end", "duration_ms": 2000,
         "usage": {"input_tokens": 3000, "output_tokens": 600, "cost": 0.03}},
        {"event_type": "tool_call"},  # no usage → not a call
    ])
    s = launcher._summarise_spend(root, "inst")
    assert s["n_llm_calls"] == 2
    assert s["input_tokens"] == 3000 and s["output_tokens"] == 600
    assert s["total_cost_usd"] == 0.03
    assert s["llm_seconds"] == 2.0


def test_summarise_spend_reports_none_cost_when_never_priced(tmp_path):
    launcher = _load_launcher()
    root = _write_trace(tmp_path, [
        {"event_type": "agent_call", "usage": {"input_tokens": 10, "output_tokens": 5}},
    ])
    s = launcher._summarise_spend(root, "inst")
    assert s["n_llm_calls"] == 1
    assert s["total_cost_usd"] is None  # no price table anywhere → don't imply a real $0


def test_summarise_spend_never_raises_on_a_missing_trace(tmp_path):
    launcher = _load_launcher()
    s = launcher._summarise_spend(str(tmp_path), "inst")
    assert s["n_llm_calls"] == 0 and s["input_tokens"] == 0
