"""Real data/validation/BenchmarkManager flow, without an Environment or model."""

import json
import uuid
from functools import partial
from pathlib import Path

import pytest

from agentevolver.benchmark import BenchmarkManager, Task
from agentevolver.data import FactorMarketDataset
from agentevolver.benchmark.default.factor_mining.bridge import request_validation
from agentevolver.benchmark.default.factor_mining.research import (
    FactorSpec, ResearchProtocol, StrategySpec, factor_report, strategy_backtest,
)


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "private_data"
    FactorMarketDataset.bundle(FactorMarketDataset.synthetic(bars=1800, assets=3), root)
    return root


FACTOR = {"name": "momentum", "expression": "delta(close, 1) / delay(close, 1)"}


async def study(tmp_path, dataset, **kwargs):
    manager = BenchmarkManager()
    await manager.configure("factor_mining", path=str(dataset),
                            base_dir=str(tmp_path / "benchmark"), **kwargs)
    task = await manager.reset("factor_mining")
    prepared = await manager.prepare("factor_mining", task)
    validate = partial(request_validation, Path(prepared.workspace_dir) / "bridge")
    return manager, task, prepared, validate


def training_inputs(prepared):
    root = Path(prepared.workspace_dir)
    manifest = json.loads((root / "study.json").read_text())
    panel = FactorMarketDataset.load(root / "train", frequency=manifest["frequency"],
                                    symbols=manifest["symbols"])
    return panel, ResearchProtocol.model_validate(manifest["protocol"])


@pytest.mark.asyncio
async def test_factor_admission_final_score_and_no_test_feedback(tmp_path, dataset):
    manager, task, prepared, validate = await study(tmp_path, dataset, goal="factors")
    try:
        assert (await manager.stats("factor_mining")).attempted == 0
        assert not (tmp_path / "benchmark/study/test_equity.parquet").exists()
        from pathlib import Path
        assert not list(Path(prepared.workspace_dir).rglob("test"))
        assert not list(Path(prepared.workspace_dir).rglob("valid"))
        panel, protocol = training_inputs(prepared)
        assert factor_report(panel, FactorSpec(**FACTOR), protocol)["passed"]
        assert not (await validate({"kind": "library"}))["factors"]  # train is not admission
        validation = await validate({"kind": "factors", "candidates": [FACTOR]})
        assert validation["reports"][0]["passed"]
        state = await validate({"kind": "library"})
        assert state["factors"]["momentum"]["version"] == 1
        assert state["remaining_validations"] == 7
        assert (await manager.stats("factor_mining")).attempted == 0  # validation has its own ledger
        await manager.submit("factor_mining", task, output={"kind": "factors", "names": ["momentum"]})
        graded = await manager.eval("factor_mining", task)
        assert graded.score == 1, graded.evaluation
        assert graded.evaluation.details["split"] == "test"
        repeat = await manager.eval("factor_mining", task)
        assert repeat.score == 1
        assert (await manager.stats("factor_mining")).attempted == 1
        assert (tmp_path / "benchmark/study/final_report.md").exists()
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_strategy_feedback_cannot_lower_factor_thresholds(tmp_path, dataset):
    manager, task, prepared, validate = await study(tmp_path, dataset)
    try:
        diagnosis = {"id": "gap0", "kind": "coverage_gap", "description": "Need a trend signal alongside volume.",
                     "evidence": "The library is empty.", "requested_direction": "Explore causal momentum."}
        await validate({"kind": "diagnosis", "diagnosis": diagnosis})
        response = await validate({"kind": "factors", "candidates": [{**FACTOR, "triggered_by_gap": "gap0"}]})
        assert response["reports"][0]["passed"]
        state = await validate({"kind": "library"})
        assert state["factors"]["momentum"]["spec"]["triggered_by_gap"] == "gap0"
        with pytest.raises(ValueError, match="forbidden"):
            panel, protocol = training_inputs(prepared)
            strategy_backtest(panel, StrategySpec(name="bad", expression="volume"), state["factors"], protocol)
        response = await validate({"kind": "strategy", "candidates": [{"name": "trend", "expression": "momentum",
                                                                      "position_rule": "threshold"}]})
        assert response["reports"][0]["passed"], response
        await manager.submit("factor_mining", task, output={"kind": "strategy", "name": "trend"})
        result = await manager.eval("factor_mining", task)
        assert result.score == 1, result.evaluation
        assert result.evaluation.details["strategy"]["used_factors"] == ["momentum"]
        assert (tmp_path / "benchmark/study/test_equity.parquet").exists()
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_quota_and_replay_survive_manager_restart(tmp_path, dataset):
    settings = dict(path=str(dataset), base_dir=str(tmp_path / "validator"),
                    study_dir=str(tmp_path / "authority"), phase="valid",
                    protocol={"validation_budget": 1})
    manager = BenchmarkManager()
    await manager.configure("factor_mining", **settings)
    task = Task(task_id=uuid.uuid4().hex, result={"kind": "factors", "candidates": [FACTOR]})
    first = await manager.eval("factor_mining", task)
    assert first.score == 1
    await manager.cleanup()
    manager = BenchmarkManager()
    await manager.configure("factor_mining", **settings)
    repeated = await manager.eval("factor_mining", task)
    assert repeated.score == 1
    new = await manager.eval("factor_mining", Task(task_id=uuid.uuid4().hex, result=task.result))
    assert new.score is None and "budget exhausted" in new.evaluation.details["error_details"]
    await manager.cleanup()


@pytest.mark.asyncio
async def test_no_final_without_freeze_and_no_forged_admission(tmp_path, dataset):
    manager, task, _, _ = await study(tmp_path, dataset, goal="factors")
    try:
        task.result = {"kind": "factors", "names": ["made_up"]}
        result = await manager.eval("factor_mining", task)
        assert result.score is None and "submit" in result.evaluation.details["error_details"]
        await manager.submit("factor_mining", task)
        result = await manager.eval("factor_mining", task)
        assert result.score == 0
        assert not result.evaluation.details["checks"]["made_up/admitted"]
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_changing_dataset_or_thresholds_requires_new_study(tmp_path, dataset):
    manager = BenchmarkManager()
    await manager.configure("factor_mining", path=str(dataset), base_dir=str(tmp_path / "bench"))
    await manager.reset("factor_mining")
    await manager.cleanup()
    await manager.configure("factor_mining", path=str(dataset), base_dir=str(tmp_path / "bench"),
                            protocol={"min_rank_ic": .001})
    with pytest.raises(ValueError, match="protocol changed"):
        await manager.reset("factor_mining")
    await manager.cleanup()


@pytest.mark.asyncio
async def test_submission_hash_is_checked(tmp_path, dataset):
    manager, task, prepared, _ = await study(tmp_path, dataset, goal="factors")
    try:
        await manager.submit("factor_mining", task, output={"kind": "factors", "names": []})
        from pathlib import Path
        path = Path(prepared.submission_path)
        value = json.loads(path.read_text())
        value["answer"]["names"] = ["forged"]
        path.write_text(json.dumps(value))
        result = await manager.eval("factor_mining", task)
        assert result.score is None
        assert "digest" in result.evaluation.details["error_details"]
    finally:
        await manager.cleanup()


@pytest.mark.asyncio
async def test_asset_scope_cannot_change_on_resume(tmp_path, dataset):
    manager = BenchmarkManager()
    settings = {"path": str(dataset), "base_dir": str(tmp_path / "bench")}
    await manager.configure("factor_mining", **settings)
    await manager.reset("factor_mining")
    await manager.cleanup()
    path = dataset / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["symbols"] = manifest["symbols"][:2]
    path.write_text(json.dumps(manifest))
    await manager.configure("factor_mining", **settings)
    with pytest.raises(ValueError, match="protocol changed"):
        await manager.reset("factor_mining")
    await manager.cleanup()
