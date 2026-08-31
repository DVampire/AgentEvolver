"""The ProgramBench eval bridge lets the single agent ask the real hidden suite to score
its current build — but only through a host-mediated file bridge that returns the NAMES of
failing tests and counts, never an expected output. These tests pin the two properties
that make it legitimate and safe: (1) it degrades to a no-op outside a launcher, is
rate-limited, and round-trips a request/response; (2) neither the tool's reply nor the
host grader's report ever carries an expected output — only test names and counts — so the
answer key cannot leak even though the grader saw it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os

from agentevolver.tool.default.programbench_eval import (
    _DEFAULT_BUDGET,
    ProgramBenchEvalTool,
    _format_result,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_launcher():
    """Import examples/run_programbench.py as a module (it is not on the package path)."""
    spec = importlib.util.spec_from_file_location(
        "rpb_under_test", os.path.join(ROOT, "examples", "run_programbench.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(tool, **kwargs):
    return asyncio.run(tool.__call__(**kwargs))


# --------------------------------------------------------------------------- tool


def test_it_is_a_no_op_without_a_bridge(monkeypatch):
    monkeypatch.delenv("AGENTEVOLVER_EVAL_BRIDGE", raising=False)
    resp = _call(ProgramBenchEvalTool(), focus="anything")
    assert not resp.success
    assert "only available inside a ProgramBench launcher" in resp.message


def test_the_budget_is_enforced(tmp_path, monkeypatch):
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    # Pre-seed as many requests as the budget allows; the next call must refuse.
    for i in range(_DEFAULT_BUDGET):
        (bridge / f"request-{i}.json").write_text("{}")
    monkeypatch.setenv("AGENTEVOLVER_EVAL_BRIDGE", str(bridge))
    monkeypatch.setenv("AGENTEVOLVER_EVAL_BUDGET", str(_DEFAULT_BUDGET))
    resp = _call(ProgramBenchEvalTool(), focus="one too many")
    assert not resp.success
    assert "budget exhausted" in resp.message.lower()


def test_it_round_trips_a_request_and_reads_the_response(tmp_path, monkeypatch):
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    # The watcher would write this; pre-seed it so the tool returns without blocking.
    (bridge / "response-0.json").write_text(
        json.dumps(
            {
                "pass": 80,
                "fail": 2,
                "total": 82,
                "fail_names": [
                    "tests.test_version.test_v_exact",
                    "tests.test_flags.test_bad_color",
                ],
                "error_code": None,
            }
        )
    )
    monkeypatch.setenv("AGENTEVOLVER_EVAL_BRIDGE", str(bridge))
    monkeypatch.setenv("AGENTEVOLVER_EVAL_BUDGET", "4")
    resp = _call(ProgramBenchEvalTool(), focus="flags")
    assert resp.success
    # The request was written for the host, numbered 0.
    assert (bridge / "request-0.json").is_file()
    # The failing names are surfaced, and the counts, and nothing that looks like an answer.
    assert "tests.test_version.test_v_exact" in resp.message
    assert "PASS=80/82" in resp.message


# --------------------------------------------------------------------------- formatting / GT-safety


def test_format_result_shows_names_and_counts_only():
    msg = _format_result(
        1,
        4,
        {
            "pass": 5,
            "fail": 3,
            "total": 8,
            "fail_names": ["a.test_one", "b.test_two", "c.test_three"],
            "error_code": None,
        },
    )
    assert "PASS=5/8 FAIL=3" in msg
    assert "Evals left after this: 2" in msg
    for n in ("a.test_one", "b.test_two", "c.test_three"):
        assert n in msg
    # The guidance sends the agent back to the reference oracle, not to an expected output.
    assert "reference_executable" in msg


def test_format_result_never_truncates_the_failing_list():
    # The failing names are the agent's complete to-do list — a truncated one silently drops
    # tests it would otherwise fix, so every name must appear no matter how many there are.
    names = [f"pkg.test_{i}" for i in range(200)]
    msg = _format_result(
        0, 4, {"pass": 0, "fail": 200, "total": 200, "fail_names": names, "error_code": None}
    )
    assert "FAIL=200" in msg
    assert "more." not in msg  # no "… and N more" truncation marker
    for n in names:  # every single one is present
        assert n in msg


def test_format_result_surfaces_a_build_error():
    msg = _format_result(
        0, 4, {"error_code": "compile_failed", "error_details": "gcc: no such file"}
    )
    assert "could not score" in msg
    assert "compile_failed" in msg


# --------------------------------------------------------------------------- host grader parsing / GT-safety


def test_grade_once_returns_names_and_counts_never_expected_outputs(tmp_path, monkeypatch):
    """`_grade_once` must reduce the grader's eval.json to names + counts. Even if a future
    eval.json carried expected/actual fields, nothing but the test name may cross back."""
    launcher = _load_launcher()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    scoring_root = tmp_path / "score"

    # Don't actually package or shell out to the grader: stub collect_submission, and make
    # the fake `programbench eval` drop an eval.json carrying (hypothetical) expected outputs.
    monkeypatch.setattr(launcher, "collect_submission", lambda ws, dest: {"source": "stub"})

    eval_payload = {
        "error_code": None,
        "test_results": [
            {"name": "t.test_pass_one", "status": "passed", "extra": {"expected": "SECRET-A"}},
            {
                "name": "t.test_fail_one",
                "status": "failure",
                "extra": {"expected": "SECRET-B", "actual": "X"},
            },
            {"name": "t.test_fail_two", "status": "failure", "extra": {"expected": "SECRET-C"}},
        ],
    }

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        # Mimic the grader writing <eval_out>/<iid>.eval.json.
        out_index = cmd.index("-o") + 1
        out_dir = cmd[out_index]
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "inst.eval.json"), "w") as handle:
            json.dump(eval_payload, handle)

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        return _P()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    report = launcher._grade_once(str(workspace), "inst", str(scoring_root))
    assert report["pass"] == 1
    assert report["fail"] == 2
    assert report["total"] == 3
    assert set(report["fail_names"]) == {"t.test_fail_one", "t.test_fail_two"}
    # The crucial property: no expected output leaks through, anywhere in the report.
    blob = json.dumps(report)
    for secret in ("SECRET-A", "SECRET-B", "SECRET-C"):
        assert secret not in blob


def test_grade_once_reports_a_package_failure_without_raising(tmp_path, monkeypatch):
    launcher = _load_launcher()

    def boom(ws, dest):
        raise RuntimeError("git archive exploded")

    monkeypatch.setattr(launcher, "collect_submission", boom)
    report = launcher._grade_once(str(tmp_path / "ws"), "inst", str(tmp_path / "score"))
    assert report["error_code"] == "package_failed"
    assert "exploded" in report["error_details"]
