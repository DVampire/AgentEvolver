"""Train-only factor research actions, with a bounded held-out validation bridge."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import Field, PrivateAttr

from agentevolver.data.factor_mining import FactorMarketDataset
from agentevolver.environment.default.factor_mining.bridge import request_validation
from agentevolver.environment.default.factor_mining.expressions import operator_catalog
from agentevolver.environment.default.factor_mining.research import (
    Diagnosis,
    FactorSpec,
    ResearchProtocol,
    StrategySpec,
    digest,
    factor_report,
    redundancy,
    strategy_backtest,
    write_report,
)
from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment
from agentevolver.registry import ENVIRONMENT


@ENVIRONMENT.register_module(force=True)
class FactorMiningEnvironment(Environment):
    name: str = "factor_mining"
    description: str = "Mine and test causal factors and strategies on training data; request bounded independent validation."
    metadata: dict = Field(default_factory=lambda: {"has_vision": False})
    workspace: str = ""
    _panel: Any = PrivateAttr(default=None)
    _protocol: Any = PrivateAttr(default=None)
    _root: Any = PrivateAttr(default=None)
    _lock: Any = PrivateAttr(default_factory=asyncio.Lock)

    async def initialize(self):
        # Other sessions may load the environment's declaration without a study.
        if self.workspace:
            self._load()

    def _load(self):
        if self._panel is not None:
            return
        if not self.workspace:
            raise ValueError("factor_mining.workspace must be the benchmark-prepared workspace")
        self._root = Path(self.workspace).resolve()
        manifest = json.loads((self._root / "study.json").read_text())
        self._protocol = ResearchProtocol.model_validate(manifest["protocol"])
        self._panel = FactorMarketDataset.load(self._root / "train", frequency=manifest["frequency"],
                                               symbols=manifest["symbols"])

    async def _host(self, payload):
        self._load()
        return await request_validation(self._root / "bridge", payload)

    @environment_manager.action(name="describe", read_only=True,
                                description="Describe the train-only market scope, rules and research thresholds.")
    async def describe(self, ctx=None, **kwargs):
        self._load()
        return {"success": True, "symbols": self._panel.symbols, "bars": len(self._panel.index),
                "start": str(self._panel.index[0]), "end": str(self._panel.index[-1]),
                "frequency": self._panel.frequency, "fields": list(self._panel.fields),
                "protocol": self._protocol.model_dump()}

    @environment_manager.action(name="get_operator_catalog", read_only=True,
                                description="List allowed causal time-series and cross-sectional expression operators.")
    async def get_operator_catalog(self, ctx=None, **kwargs):
        return {"success": True, "operators": operator_catalog(),
                "limits": "128 AST nodes, 256 combined lookback bars; positive literal windows; no Python execution"}

    @environment_manager.action(name="inspect_data", read_only=True,
                                description="Inspect a bounded TRAIN data sample and field distributions; never reads held-out splits.")
    async def inspect_data(self, offset: int = 0, rows: int = 5, ctx=None, **kwargs):
        self._load()
        if not 0 <= offset < len(self._panel.index) or not 1 <= rows <= 20:
            raise ValueError("offset must be within train; rows must be between 1 and 20")
        sample = self._panel.slice(offset, offset + rows).records()
        # JSON conversion explicitly maps missing values to null.
        records = json.loads(sample.to_json(orient="records", date_format="iso"))
        distributions = {name: json.loads(frame.describe().to_json())
                         for name, frame in self._panel.fields.items()}
        return {"success": True, "split": "train", "records": records, "distributions": distributions}

    @environment_manager.action(name="get_factor_library", read_only=True,
                                description="Read the host-authoritative admitted factors, strategy validations, diagnoses and remaining quota.")
    async def get_factor_library(self, ctx=None, **kwargs):
        return await self._host({"kind": "library"})

    @environment_manager.action(name="run_factor_backtest",
                                description="Evaluate a batch of factor specifications on TRAIN only; does not admit them to the library.")
    async def run_factor_backtest(self, candidates: list[dict], ctx=None, **kwargs):
        self._load()
        if not 1 <= len(candidates) <= self._protocol.max_batch:
            raise ValueError(f"batch must contain 1..{self._protocol.max_batch} candidates")
        reports = []
        # Train reports have their own content-addressed paths; no caller-controlled path.
        async with self._lock:
            for candidate in candidates:
                try:
                    spec = FactorSpec.model_validate(candidate)
                    report = await asyncio.to_thread(factor_report, self._panel, spec, self._protocol)
                    write_report(self._root / "reports" / f"factor-{digest(candidate)[:20]}", report)
                    reports.append(report)
                except ValueError as exc:
                    reports.append({"passed": False, "error": str(exc)})
        return {"success": True, "split": "train", "reports": reports}

    @environment_manager.action(name="check_correlation", read_only=True,
                                description="Check structural variants and train-only numeric redundancy against admitted factors.")
    async def check_correlation(self, candidate: dict, ctx=None, **kwargs):
        state = await self.get_factor_library()
        spec = FactorSpec.model_validate(candidate)
        result = await asyncio.to_thread(redundancy, self._panel, spec, state["factors"], self._protocol)
        return {"success": True, **result}

    @environment_manager.action(name="run_strategy_backtest",
                                description="Backtest an admitted-factor combination on TRAIN, with next-open execution and trading costs.")
    async def run_strategy_backtest(self, strategy: dict, ctx=None, **kwargs):
        state = await self.get_factor_library()
        spec = StrategySpec.model_validate(strategy)
        report, curve = await asyncio.to_thread(strategy_backtest, self._panel, spec,
                                                 state["factors"], self._protocol)
        path = self._root / "reports" / f"strategy-{digest(strategy)[:20]}"
        write_report(path, report)
        curve.to_parquet(path.with_suffix(".parquet"))
        return {"success": True, "split": "train", "report": report}

    @environment_manager.action(name="validate",
                                description="Spend ONE host validation call: kind=factors with candidate specs, or kind=strategy with one strategy spec. Only independently passing factors are admitted.")
    async def validate(self, kind: str, candidates: list[dict], ctx=None, **kwargs):
        if kind not in ("factors", "strategy"):
            raise ValueError("kind must be factors or strategy")
        return await self._host({"kind": kind, "candidates": candidates})

    @environment_manager.action(name="record_diagnosis",
                                description="Record a qualitative strategy gap with evidence for the next factor-mining round; cannot change acceptance thresholds.")
    async def record_diagnosis(self, diagnosis: dict, ctx=None, **kwargs):
        value = Diagnosis.model_validate(diagnosis)
        return await self._host({"kind": "diagnosis", "diagnosis": value.model_dump()})

    async def get_state(self, ctx=None, **kwargs):
        if not self.workspace:
            return {"success": True, "state": "No factor research study is configured."}
        self._load()
        return {"success": True, "state": f"Train-only factor research: {len(self._panel.symbols)} assets, "
                f"{len(self._panel.index)} bars. Query the library before combining factors. "
                "Held-out grading is host-controlled; test is unavailable during research."}

    async def cleanup(self):
        self._panel = None
