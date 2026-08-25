"""When a MetaAgent dispatches a worker, it sees only the worker's final text
(`resp.message`). That text cannot tell it whether the worker *finished* or ran out of
budget mid-work: a worker that hit its step ceiling returns its last words exactly as one
that called done_tool returns its summary, so a partial reconstruction reads as a complete
one. `_delegation_summary` appends a compact, deterministic envelope built from facts the
run already recorded (`resp.data`), so the outcome and cost travel back with the result.
"""

from __future__ import annotations

from agentevolver.agent.types import _delegation_summary


def test_a_clean_finish_is_labelled_finished_with_its_cost():
    env = _delegation_summary({"done": True, "stopped_by_constraint": False,
                               "step": 40, "max_step": 50})
    assert "finished" in env
    assert "40/50 steps" in env
    assert "PARTIAL" not in env


def test_hitting_the_step_ceiling_is_flagged_partial():
    # The case that motivated this: done never set, step == max_step.
    env = _delegation_summary({"done": False, "stopped_by_constraint": False,
                               "step": 50, "max_step": 50})
    assert "PARTIAL" in env
    assert "step ceiling" in env
    assert "50/50 steps" in env


def test_a_constraint_stop_is_flagged_partial():
    env = _delegation_summary({"done": False, "stopped_by_constraint": True,
                               "step": 30, "max_step": 100})
    assert "PARTIAL" in env
    assert "resource limit" in env


def test_an_unfinished_stop_short_of_the_ceiling_is_partial():
    env = _delegation_summary({"done": False, "stopped_by_constraint": False,
                               "step": 12, "max_step": 100})
    assert "PARTIAL" in env
    assert "12/100 steps" in env


def test_an_unbounded_child_does_not_show_a_sentinel_denominator():
    # max_step <= 0 is stored as 1e8; "used 5/100000000 steps" is noise, so the denominator
    # is suppressed while the step count it did run is still reported.
    env = _delegation_summary({"done": True, "step": 5, "max_step": int(1e8)})
    assert "100000000" not in env
    assert "used 5 steps" in env


def test_missing_data_yields_no_envelope():
    # Never fabricate an envelope from nothing — a missing data blob adds no line.
    assert _delegation_summary(None) == ""
    assert _delegation_summary({}) == ""


def test_the_envelope_is_a_single_trailing_tagged_line():
    # It must be appendable to the child's message without disturbing it: a blank-line gap
    # then one bracketed tag, nothing before the child's own text.
    env = _delegation_summary({"done": True, "step": 1, "max_step": 5})
    assert env.startswith("\n\n[dispatch status:")
    assert env.rstrip().endswith("]")
    assert env.count("[dispatch status:") == 1
