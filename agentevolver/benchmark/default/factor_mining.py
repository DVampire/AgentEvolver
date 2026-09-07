"""Independent factor/strategy acceptance, with private validation and final test data."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, PrivateAttr

from agentevolver.benchmark.types import Benchmark, EvaluationResult, Stats, Task
from agentevolver.data.factor_mining import FactorMarketDataset
from agentevolver.environment.default.factor_mining.research import (
    Diagnosis,
    FactorSpec,
    ResearchProtocol,
    StrategySpec,
    digest,
    factor_report,
    redundancy,
    retention_checks,
    strategy_backtest,
    write_report,
)
from agentevolver.registry import BENCHMARK
from agentevolver.utils.file_utils import atomic_write_text, file_lock


def _write(path: Path, value: dict):
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def _minimum_ic(report):
    values = [m["rank_ic"] for h in report["metrics"].values() for m in h["per_asset"].values()]
    return min(values) if values and all(v is not None for v in values) else None


@BENCHMARK.register_module(force=True)
class FactorMiningBenchmark(Benchmark):
    """Mine independent factors and combine them under a fixed multi-asset protocol."""

    name: str = "factor_mining"
    path: str = ""  # User-owned data bundle; no hidden network download.
    protocol: dict = Field(default_factory=dict)
    phase: Literal["valid", "test"] = "test"
    goal: Literal["factors", "strategy"] = "strategy"
    study_dir: str = ""
    submission_protocol: str = "factor-research-v1"
    _panel: dict = PrivateAttr(default_factory=dict)
    _manifest: dict = PrivateAttr(default_factory=dict)
    _policy: Any = PrivateAttr(default=None)
    _study: Any = PrivateAttr(default=None)
    _bridge_tasks: dict = PrivateAttr(default_factory=dict)
    _validators: dict = PrivateAttr(default_factory=dict)

    async def _initialize(self):
        if not self.path:
            raise ValueError("factor_mining needs a market bundle prepared by the data module")
        root = Path(self.path).resolve()
        self._manifest = json.loads((root / "manifest.json").read_text())
        actual = await asyncio.to_thread(FactorMarketDataset.fingerprint, root)
        if actual != self._manifest["sha256"]:
            raise ValueError("dataset fingerprint mismatch")
        self._policy = ResearchProtocol.model_validate(self.protocol)
        self._study = Path(self.study_dir or str(Path(self.base_dir) / "study")).resolve()
        self._study.mkdir(parents=True, exist_ok=True)
        contract = {"dataset": actual,
                    "data_manifest": {k: self._manifest[k] for k in ("frequency", "symbols", "splits")},
                    "protocol": self._policy.model_dump(), "goal": self.goal,
                    "engine": "factor-research-v1"}
        contract_path = self._study / "contract.json"
        async with file_lock(str(contract_path) + ".lock"):
            if contract_path.exists() and json.loads(contract_path.read_text()) != contract:
                raise ValueError("study dataset/protocol changed; use a new study directory")
            _write(contract_path, contract)
        # Never load the test panel while the agent is iterating.
        for split in ("train", "valid"):
            self._panel[split] = await asyncio.to_thread(
                FactorMarketDataset.load, root / split,
                frequency=self._manifest["frequency"], symbols=self._manifest["symbols"])
        bounds = self._manifest["splits"]
        import pandas as pd

        if not (self._panel["train"].index[-1] < self._panel["valid"].index[0]
                and self._panel["valid"].index[-1] < pd.Timestamp(bounds["test"]["start"])):
            raise ValueError("train/valid/test must be chronologically disjoint")
        for split in ("train", "valid"):
            panel = self._panel[split]
            if str(panel.index[0]) != bounds[split]["start"] or str(panel.index[-1]) != bounds[split]["end"]:
                raise ValueError("split manifest bounds do not match the data")

    def _state(self):
        path = self._study / "state.json"
        return json.loads(path.read_text()) if path.exists() else {
            "factors": {}, "strategies": {}, "diagnoses": {}, "requests": {}, "version": 0,
        }

    def _public_state(self):
        state = self._state()
        return {"success": True, "version": state["version"],
                "remaining_validations": max(0, self._policy.validation_budget - len(state["requests"])),
                "factors": {name: {"spec": entry["spec"], "version": entry["version"],
                                    "train_min_rank_ic": _minimum_ic(entry["train"]),
                                    "valid_min_rank_ic": _minimum_ic(entry["valid"])}
                            for name, entry in state["factors"].items()},
                "strategies": {name: {"spec": entry["spec"], "passed": entry["passed"],
                                       "quality": entry["quality"], "used_factors": entry["used_factors"]}
                               for name, entry in state["strategies"].items()},
                "diagnoses": state["diagnoses"]}

    async def _step(self):
        if self._index:
            return None
        self._index += 1
        return Task(task_id="factor-study", input=f"Mine {self.goal} across all configured assets. "
                    "Use train for exploration, bounded independent validation for selection, "
                    "and submit one frozen result for final test.",
                    extra={"symbols": self._manifest["symbols"], "goal": self.goal})

    def _task_payload(self, record):
        return {k: record[k] for k in ("symbols", "goal") if k in record}

    async def _prepare(self, task, ctx, options):
        if self.phase != "test" or task.task_id != "factor-study":
            raise ValueError("only the final study task creates a research workspace")
        if (self._study / "final.json").exists():
            raise ValueError("study is already finalized; use resume to read its result")
        root = Path(ctx.workspace_dir).resolve()
        private = Path(self.path).resolve()
        if root == private or root in private.parents or private in root.parents or root in self._study.parents:
            raise ValueError("agent workspace must not contain or share the private data/study tree")
        root.mkdir(parents=True, exist_ok=True)
        public = {"frequency": self._manifest["frequency"], "symbols": self._manifest["symbols"],
                  "dataset_sha256": self._manifest["sha256"], "protocol": self._policy.model_dump(),
                  "goal": self.goal}
        manifest_path = root / "study.json"
        if manifest_path.exists() and json.loads(manifest_path.read_text()) != public:
            raise ValueError("workspace belongs to a different study")
        if not (root / "train").exists():
            shutil.copytree(private / "train", root / "train")
        if FactorMarketDataset.fingerprint(root / "train") != FactorMarketDataset.fingerprint(private / "train"):
            raise ValueError("workspace train data was changed")
        _write(manifest_path, public)
        bridge = root / "bridge"
        for folder in ("requests", "responses"):
            (bridge / folder).mkdir(parents=True, exist_ok=True)
        from agentevolver.benchmark.server import BenchmarkManager

        validator = BenchmarkManager()
        await validator.configure("factor_mining", path=self.path, protocol=self.protocol,
                                  phase="valid", goal=self.goal, study_dir=str(self._study),
                                  base_dir=str(self._study / "validation"), resume=True)
        self._validators[task.task_id] = validator
        self._bridge_tasks[task.task_id] = asyncio.create_task(self._serve_bridge(bridge, validator))
        ctx.payload.update(public)
        ctx.payload["workspace"] = str(root)

    async def _serve_bridge(self, root, manager):
        while True:
            for request in sorted((root / "requests").glob("*.json")):
                if not re.fullmatch(r"[0-9a-f]{32}\.json", request.name):
                    continue
                response = root / "responses" / request.name
                if response.exists():
                    continue
                try:
                    if request.is_symlink() or request.stat().st_size > 64 * 1024:
                        raise ValueError("invalid bridge request")
                    payload = json.loads(request.read_text())
                    if not isinstance(payload, dict):
                        raise ValueError("request must be an object")
                    if payload.get("kind") == "library":
                        value = self._public_state()
                    elif payload.get("kind") == "diagnosis":
                        diagnosis = Diagnosis.model_validate(payload.get("diagnosis"))
                        async with file_lock(str(self._study / "state.lock")):
                            state = self._state()
                            if len(state["diagnoses"]) >= 32 and diagnosis.id not in state["diagnoses"]:
                                raise ValueError("diagnosis limit reached")
                            previous = state["diagnoses"].get(diagnosis.id)
                            if previous and previous != diagnosis.model_dump():
                                raise ValueError("diagnosis ids are immutable")
                            state["diagnoses"][diagnosis.id] = diagnosis.model_dump()
                            _write(self._study / "state.json", state)
                        value = {"success": True, "diagnosis": diagnosis.model_dump()}
                    else:
                        evaluated = await manager.eval("factor_mining", Task(task_id=request.stem, result=payload))
                        if evaluated.evaluation.status == "error":
                            value = {"success": False, "message": evaluated.evaluation.details.get("error_details", "validation failed")}
                        else:
                            value = {"success": True, **evaluated.evaluation.details}
                    if response.is_symlink():
                        raise ValueError("invalid response path")
                    _write(response, value)
                except Exception as exc:
                    _write(response, {"success": False, "message": str(exc)})
            await asyncio.sleep(.05)

    async def _eval(self, task):
        if self.phase == "valid":
            report = await self._validation(task)
        else:
            report = await self._final(task)
        task.evaluation = EvaluationResult.from_report(report, float(report["passed"]))
        task.score = task.evaluation.score
        return task

    async def _validation(self, task):
        if not re.fullmatch(r"[0-9a-f]{32}", task.task_id):
            raise ValueError("validation needs a unique request id")
        async with file_lock(str(self._study / "state.lock")):
            if (self._study / "final.json").exists():
                raise ValueError("validation is closed after final submission")
            state = self._state()
            identity = digest(task.result)
            previous = state["requests"].get(task.task_id)
            if previous:
                if previous["digest"] != identity or previous["report"] is None:
                    raise ValueError("changed or interrupted validation request; quota remains consumed")
                return previous["report"]
            if len(state["requests"]) >= self._policy.validation_budget:
                raise ValueError("validation budget exhausted")
            # Reserve before computation, so exceptions/restarts cannot reset the budget.
            state["requests"][task.task_id] = {"digest": identity, "report": None}
            _write(self._study / "state.json", state)
            report = await asyncio.to_thread(self._validate_candidates, task.result, state)
            report["remaining_validations"] = self._policy.validation_budget - len(state["requests"])
            state["requests"][task.task_id]["report"] = report
            _write(self._study / "state.json", state)
            _write(self._study / "factor_library.json", {
                "version": state["version"], "factors": state["factors"],
                "dataset_sha256": self._manifest["sha256"], "protocol": self._policy.model_dump(),
            })
            lines = ["# Admitted factor library", "", f"Version: {state['version']}", "",
                     "Factors are immutable and passed train, validation, retention and deduplication.",
                     "Strategies reference these names; test results are separate final evidence.", "",
                     "| Name | Expression | Direction | Version | Diagnosis |",
                     "| --- | --- | --- | --- | --- |"]
            for name, entry in state["factors"].items():
                spec = entry["spec"]
                lines.append(f"| {name} | `{spec['expression']}` | {spec['direction']} | "
                             f"{entry['version']} | {spec['triggered_by_gap'] or '-'} |")
            atomic_write_text(self._study / "FACTOR-LIBRARY.md", "\n".join(lines) + "\n")
            write_report(self._study / "reports" / task.task_id, report)
            return report

    def _validate_candidates(self, payload, state):
        if not isinstance(payload, dict) or set(payload) != {"kind", "candidates"}:
            raise ValueError("validation accepts only kind and candidates")
        kind, candidates = payload["kind"], payload["candidates"]
        if kind not in ("factors", "strategy") or not isinstance(candidates, list) or not 1 <= len(candidates) <= self._policy.max_batch:
            raise ValueError("invalid candidate batch")
        if kind == "strategy" and len(candidates) != 1:
            raise ValueError("validate one strategy per call")
        reports = []
        for candidate in candidates:
            try:
                if kind == "factors":
                    spec = FactorSpec.model_validate(candidate)
                    if spec.name in state["factors"]:
                        raise ValueError("admitted factor names are immutable")
                    if spec.triggered_by_gap and spec.triggered_by_gap not in state["diagnoses"]:
                        raise ValueError("unknown diagnosis id")
                    duplicate = redundancy(self._panel["train"], spec, state["factors"], self._policy)
                    if duplicate["redundant"]:
                        reports.append({"passed": False, "name": spec.name, "redundancy": duplicate})
                        continue
                    train = factor_report(self._panel["train"], spec, self._policy)
                    valid = factor_report(self._panel["valid"], spec, self._policy)
                    decay = retention_checks(train, valid, self._policy)
                    passed = train["passed"] and valid["passed"] and all(decay.values())
                    entry = {"spec": spec.model_dump(), "train": train, "valid": valid,
                             "checks": decay, "redundancy": duplicate, "passed": passed,
                             "version": state["version"] + 1}
                    if passed:
                        state["version"] += 1
                        state["factors"][spec.name] = entry
                    reports.append({"name": spec.name, **entry})
                else:
                    spec = StrategySpec.model_validate(candidate)
                    old = state["strategies"].get(spec.name)
                    if old and old["spec"] != spec.model_dump():
                        raise ValueError("use a new strategy name for each revision")
                    train, _ = strategy_backtest(self._panel["train"], spec, state["factors"], self._policy)
                    valid, _ = strategy_backtest(self._panel["valid"], spec, state["factors"], self._policy)
                    values = [v["sharpe"] for v in valid["per_asset"].values()]
                    quality = min(values) if all(v is not None for v in values) else None
                    entry = {"spec": spec.model_dump(), "train": train, "valid": valid,
                             "passed": train["passed"] and valid["passed"],
                             "quality": quality, "used_factors": valid["used_factors"]}
                    state["strategies"][spec.name] = entry
                    reports.append({"name": spec.name, **entry})
            except ValueError as exc:
                reports.append({"passed": False, "error": str(exc)})
        return {"kind": "validation", "split": "valid", "reports": reports,
                "passed": bool(reports) and all(r["passed"] for r in reports)}

    async def _collect_submission(self, task, ctx, output):
        if task.task_id != "factor-study" or not isinstance(output, dict):
            raise ValueError("expected a structured study submission")
        if self.goal == "strategy":
            if set(output) != {"kind", "name"} or output["kind"] != "strategy":
                raise ValueError("submit {kind: strategy, name: validated_strategy_name}")
        elif set(output) != {"kind", "names"} or output["kind"] != "factors":
            raise ValueError("submit {kind: factors, names: [admitted_factor_names]}")
        return await super()._collect_submission(task, ctx, output)

    async def _final(self, task):
        if task.task_id != "factor-study" or not (task.extra or {}).get("submission_sha256"):
            raise ValueError("final test requires BenchmarkManager.submit before eval")
        async with file_lock(str(self._study / "state.lock")):
            final_path = self._study / "final.json"
            identity = task.extra["submission_sha256"]
            if final_path.exists():
                previous = json.loads(final_path.read_text())
                if previous["submission"] != identity:
                    raise ValueError("final test already consumed by a different submission")
                if previous.get("report") is None:
                    raise ValueError("final evaluation interrupted; test cannot be queried again")
                return previous["report"]
            current_fingerprint = await asyncio.to_thread(FactorMarketDataset.fingerprint, self.path)
            if current_fingerprint != self._manifest["sha256"]:
                raise ValueError("market data changed during research; refusing to score")
            _write(final_path, {"submission": identity, "report": None})
            test = await asyncio.to_thread(FactorMarketDataset.load, Path(self.path) / "test",
                                           frequency=self._manifest["frequency"],
                                           symbols=self._manifest["symbols"])
            if test.index[0] <= self._panel["valid"].index[-1]:
                raise ValueError("test overlaps validation")
            state = self._state()
            result = task.result
            report = {"kind": "final", "split": "test", "passed": False, "checks": {},
                      "dataset_sha256": self._manifest["sha256"],
                      "data_source": self._manifest.get("source", {"kind": "unspecified"}),
                      "submission_sha256": identity, "protocol": self._policy.model_dump()}
            if self.goal == "strategy":
                entry = state["strategies"].get(result.get("name"))
                report["checks"]["validated_strategy"] = bool(entry and entry["passed"])
                names = entry["used_factors"] if entry else []
                if entry:
                    strategy, curve = await asyncio.to_thread(strategy_backtest, test,
                        StrategySpec.model_validate(entry["spec"]), state["factors"], self._policy)
                    report["strategy"] = strategy
                    report["checks"]["strategy_test"] = strategy["passed"]
                    curve.to_parquet(self._study / "test_equity.parquet")
            else:
                names = result.get("names", [])
            if not isinstance(names, list) or not names or len(names) != len(set(names)):
                report["checks"]["nonempty_unique_factors"] = False
                names = []
            factor_reports = {}
            for name in names:
                if name not in state["factors"]:
                    report["checks"][f"{name}/admitted"] = False
                    continue
                spec = FactorSpec.model_validate(state["factors"][name]["spec"])
                evaluated = await asyncio.to_thread(factor_report, test, spec, self._policy)
                factor_reports[name] = evaluated
                report["checks"][f"{name}/test"] = evaluated["passed"]
            report["factors"] = factor_reports
            report["passed"] = bool(report["checks"]) and all(report["checks"].values())
            _write(final_path, {"submission": identity, "report": report})
            write_report(self._study / "final_report", report)
            return report

    async def _stats(self):
        return Stats(total=1 if self.phase == "test" else self._policy.validation_budget,
                     extra={"phase": self.phase, "admitted_factors": len(self._state()["factors"])})

    async def _stop_task_resources(self, ctx):
        watcher = self._bridge_tasks.pop(ctx.task_id, None)
        if watcher:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        validator = self._validators.pop(ctx.task_id, None)
        if validator:
            await validator.cleanup()
        await super()._stop_task_resources(ctx)

    async def _cleanup(self):
        for watcher in self._bridge_tasks.values():
            watcher.cancel()
        await asyncio.gather(*self._bridge_tasks.values(), return_exceptions=True)
        for validator in self._validators.values():
            await validator.cleanup()
        self._bridge_tasks.clear()
        self._validators.clear()
        self._panel.clear()
