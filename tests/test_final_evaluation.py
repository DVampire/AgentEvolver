"""Offline contracts for host-only final evaluation; no models or containers started."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.benchmark import BenchmarkManager
from agentevolver.benchmark.default.programbench import ProgramBenchmark
from agentevolver.benchmark.default.swebench import SWEBenchProBenchmark, SWEBenchVerifiedBenchmark
from agentevolver.benchmark.types import EvaluationResult, Task


def test_hidden_evaluation_tools_and_request_watchers_are_removed():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "agentevolver/tool/default/evaluation").exists()
    for name in ("swebench_pro", "swebench_verified", "programbench"):
        source = (root / f"examples/run_{name}.py").read_text()
        assert "def _grade_once" not in source
        assert "eval_bridge_watcher" not in source
        assert "benchmark_manager.eval(" in source
        for suffix in ("agent", "agent_baseline"):
            config = (root / f"configs/{name}_{suffix}.py").read_text()
            assert f'"{name}_eval_tool"' not in config


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", [SWEBenchProBenchmark, SWEBenchVerifiedBenchmark])
async def test_swe_evaluation_uses_frozen_patch_and_keeps_errors_unscored(tmp_path, monkeypatch, cls):
    benchmark = cls(base_dir=str(tmp_path))
    monkeypatch.setattr(benchmark, "_initialize", AsyncMock())
    benchmark._data_records = [{"instance_id": "task", "base_commit": "base", "patch": "secret"}]
    evaluate = AsyncMock(return_value={"error_code": "timeout"})
    monkeypatch.setattr(benchmark, "_evaluate", evaluate)
    submission = {"instance_id": "task", "base_commit": "base", "patch": "frozen",
                  "sha256": hashlib.sha256(b"frozen").hexdigest()}
    task = await benchmark.eval(Task(task_id="task", result=submission))
    assert task.score is None
    assert task.evaluation.status == "error"
    assert evaluate.call_args.args[1] == "frozen"
    evaluate.reset_mock()
    submission["patch"] = "modified after submission"
    rejected = await benchmark.eval(Task(task_id="task", result=submission))
    assert rejected.evaluation.status == "error"
    evaluate.assert_not_called()
    safe = await benchmark.step()
    assert safe.ground_truth is None
    assert "patch" not in safe.extra


@pytest.mark.asyncio
async def test_manager_does_not_average_evaluation_errors_as_zero():
    manager = BenchmarkManager()
    async def fail(*args):
        raise RuntimeError("grader unavailable")
    manager.benchmark_context_manager = SimpleNamespace(eval=fail, get=AsyncMock(return_value=object()))
    result = await manager.eval("demo", Task(task_id="task"))
    assert result.score is None and result.evaluation.status == "error"
    with pytest.raises(RuntimeError, match="no valid score"):
        await manager("demo", [{"task_id": "task", "prediction": "answer"}])


def test_failed_answer_still_has_a_valid_zero_score():
    result = EvaluationResult.from_report({"resolved": False})
    assert result.status == "failed" and result.score == 0.0


def test_program_submission_is_digest_bound_and_attempts_are_isolated(tmp_path):
    benchmark = ProgramBenchmark(base_dir=str(tmp_path / "benchmark"))
    artifact = tmp_path / "submission.tar.gz"
    artifact.write_bytes(b"frozen archive")
    submission = {"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    first = benchmark._prepare_submission("task", submission)
    second = benchmark._prepare_submission("task", submission)
    assert first != second
    assert (Path(first) / "task/submission.tar.gz").read_bytes() == b"frozen archive"
    with pytest.raises(ValueError, match="task mismatch"):
        benchmark._prepare_submission("task", {**submission, "instance_id": "other"})
    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        benchmark._prepare_submission("task", submission)


@pytest.mark.asyncio
async def test_program_grader_runs_asynchronously_and_preserves_failure_evidence(tmp_path, monkeypatch):
    benchmark = ProgramBenchmark(base_dir=str(tmp_path / "benchmark"))
    monkeypatch.setattr(benchmark, "_initialize", AsyncMock())
    benchmark._instances = {"task": {}}
    artifact = tmp_path / "submission.tar.gz"
    artifact.write_bytes(b"frozen")
    monkeypatch.setattr(benchmark, "_programbench_cli", lambda: "programbench")
    async def spawn(*args, **kwargs):
        assert args[1] == "eval" and kwargs["start_new_session"]
        report = Path(args[2]) / "task/task.eval.json"
        report.write_text(json.dumps({"error_code": "build_failed", "error_details": "compiler failed"}))
        async def communicate():
            await asyncio.sleep(0)
            return b"compiler output", b"compiler error"
        return SimpleNamespace(returncode=0, communicate=communicate)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = await benchmark.eval(Task(task_id="task", result={
        "path": str(artifact), "sha256": hashlib.sha256(b"frozen").hexdigest()}))
    assert task.score is None and task.evaluation.status == "error"
    assert task.evaluation.details["error_code"] == "build_failed"
    assert list((tmp_path / "benchmark/eval_runs").glob("*/stderr.log"))[0].read_text() == "compiler error"
    stats = await benchmark.stats()
    assert stats.wrong == 0 and stats.extra["evaluation_errors"] == 1
