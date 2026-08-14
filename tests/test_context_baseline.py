"""The baseline reports what was billed, not what could have been.

Prefix reuse is a proxy: it says a cache *could* hit. It once read 99% on a prompt that
was billed in full, because the only `cache_control` breakpoint sat on the system message
while the capability catalogs were rendered after the agent state. A measurement that
cannot tell those apart is the reason that went unnoticed, so the two are now reported
side by side and a disagreement is meant to be visible.
"""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "context_baseline", Path(__file__).resolve().parents[1] / "scripts" / "context_baseline.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def _call(agent, usage=None):
    row = {"event_type": "agent_call", "agent_name": agent}
    if usage is not None:
        row["usage"] = usage
    return row


def test_usage_sums_per_agent():
    """Per agent, because one run may use both context paths at once."""
    rows = [
        _call("code_agent", {"input_tokens": 100, "cache_read_tokens": 80, "cost": 0.01}),
        _call("code_agent", {"input_tokens": 100, "cache_read_tokens": 90, "cost": 0.01}),
        _call("meta_agent", {"input_tokens": 50, "cache_read_tokens": 0, "cost": 0.02}),
    ]
    out = baseline._usage_by_agent(rows)

    assert out["code_agent"]["input_tokens"] == 200
    assert out["code_agent"]["cache_read_tokens"] == 170
    assert out["code_agent"]["cache_hit_rate"] == 0.85
    assert out["meta_agent"]["cache_hit_rate"] == 0.0


def test_a_step_without_usage_is_not_counted_as_free():
    """A missing figure and a zero-cost call are different facts.

    Folding the first into the second makes a partial total read as authoritative, which
    is the failure mode a cost measurement can least afford.
    """
    out = baseline._usage_by_agent([
        _call("code_agent", {"input_tokens": 100, "cache_read_tokens": 100}),
        _call("code_agent"),                       # provider reported nothing
    ])["code_agent"]

    assert out["steps_with_usage"] == 1
    assert out["steps_without_usage"] == 1
    assert out["input_tokens"] == 100              # not diluted by the missing step
    assert out["cache_hit_rate"] == 1.0


def test_events_that_are_not_model_calls_are_ignored():
    """Tool events carry no usage and must not be counted as unreported model calls."""
    out = baseline._usage_by_agent([
        {"event_type": "tool_start", "agent_name": "code_agent"},
        {"event_type": "tool_end", "agent_name": "code_agent"},
        _call("code_agent", {"input_tokens": 10}),
    ])["code_agent"]
    assert out["steps_with_usage"] == 1
    assert out["steps_without_usage"] == 0


def test_no_usage_at_all_yields_no_rate():
    """An unmeasured run must report "unknown", never 0% — they prompt opposite actions."""
    out = baseline._usage_by_agent([_call("code_agent")])["code_agent"]
    assert out["cache_hit_rate"] is None
