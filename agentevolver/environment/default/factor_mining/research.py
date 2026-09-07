"""Research contracts, factor diagnostics, and causal multi-asset backtests."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentevolver.data.factor_mining import FIELDS, MarketPanel
from agentevolver.environment.default.factor_mining.expressions import Expression
from agentevolver.utils.file_utils import atomic_write_text


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FactorSpec(Contract):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
    expression: str = Field(min_length=1, max_length=2048)
    direction: Literal[-1, 1] = 1
    rationale: str = Field(default="", max_length=2000)
    triggered_by_gap: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def valid_expression(self):
        Expression.parse(self.expression, FIELDS)
        return self


class StrategySpec(Contract):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
    expression: str = Field(min_length=1, max_length=2048)
    position_rule: Literal["rank", "threshold", "linear"] = "rank"
    threshold: float = Field(default=0, ge=0, le=100)
    long_only: bool = False
    engine: Literal["vectorized", "stateful"] = "vectorized"
    stop_loss: float = Field(default=0.05, gt=0, lt=1)
    cooldown: int = Field(default=5, ge=1, le=256)
    rationale: str = Field(default="", max_length=2000)


class ResearchProtocol(Contract):
    horizons: list[int] = Field(default_factory=lambda: [1, 5], min_length=1, max_length=8)
    min_samples: int = Field(default=64, ge=20)
    min_rank_ic: float = Field(default=0.02, ge=0, le=1)
    max_nan_fraction: float = Field(default=0.3, ge=0, lt=1)
    max_correlation: float = Field(default=0.7, gt=0, le=1)
    min_valid_retention: float = Field(default=0.25, ge=0, le=1)
    min_sharpe: float = Field(default=0.5, ge=0)
    min_annual_return: float = Field(default=0, ge=-1)
    max_drawdown: float = Field(default=0.4, gt=0, lt=1)
    cost_bps: float = Field(default=5, ge=0, le=1000)
    slippage_bps: float = Field(default=2, ge=0, le=1000)
    periods_per_year: float = Field(default=8766, gt=0)
    validation_budget: int = Field(default=8, ge=1, le=100)
    max_batch: int = Field(default=8, ge=1, le=32)

    @model_validator(mode="after")
    def valid_horizons(self):
        if any(type(h) is not int or not 1 <= h <= 64 for h in self.horizons):
            raise ValueError("horizons must be positive integers <= 64")
        if len(set(self.horizons)) != len(self.horizons):
            raise ValueError("duplicate horizons")
        return self


class Diagnosis(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: Literal["coverage_gap", "weak_signal", "generalization_failure"]
    description: str = Field(min_length=10, max_length=2000)
    evidence: str = Field(min_length=5, max_length=2000)
    requested_direction: str = Field(min_length=5, max_length=1000)


def finite(value):
    return float(value) if value is not None and np.isfinite(value) else None


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def write_report(path: str | Path, report: dict) -> None:
    """Emit identical structured evidence in JSON and a readable Markdown companion."""
    path = Path(path)
    body = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path.with_suffix(".json"), body + "\n")
    checks = report.get("checks", {})
    lines = [f"# {report.get('kind', 'Research')} report", "",
             f"Passed: **{report.get('passed', False)}**", ""]
    lines.extend(f"- {'PASS' if passed else 'FAIL'}: {name}" for name, passed in checks.items())
    lines.extend(["", "```json", body, "```", ""])
    atomic_write_text(path.with_suffix(".md"), "\n".join(lines))


def factor_values(panel: MarketPanel, spec: FactorSpec) -> pd.DataFrame:
    return Expression.parse(spec.expression, panel.fields).evaluate(panel.fields) * spec.direction


def factor_report(panel: MarketPanel, spec: FactorSpec, protocol: ResearchProtocol) -> dict:
    expression = Expression.parse(spec.expression, panel.fields)
    values = factor_values(panel, spec)
    report = {"kind": "factor", "factor": spec.model_dump(), "paradigm": expression.paradigm,
              "structure": expression.structure, "symbols": panel.symbols,
              "metrics": {}, "checks": {}}
    for horizon in protocol.horizons:
        # close[t] features -> trade at open[t+1] -> exit at open[t+h+1].
        # Construct inside each split so training targets never reach held-out bars.
        target = panel.fields["open"].shift(-horizon - 1) / panel.fields["open"].shift(-1) - 1
        x = values.iloc[expression.lookback: -horizon - 1]
        y = target.reindex(x.index)
        mask = x.notna() & y.notna()
        x, y = x.where(mask), y.where(mask)
        counts = mask.sum()
        ic = x.corrwith(y)
        rank_ic = x.rank().corrwith(y.rank())
        # Nonoverlapping temporal blocks: a stability diagnostic, not a t-statistic.
        blocks = [x.iloc[i:i + 32].rank().corrwith(y.iloc[i:i + 32].rank())
                  for i in range(0, max(0, len(x) - 31), 32)]
        block_frame = pd.DataFrame(blocks)
        ir = (block_frame.mean() / block_frame.std().replace(0, np.nan)
              if len(blocks) > 1 else pd.Series(np.nan, index=x.columns))
        cross = x.rank(axis=1).corrwith(y.rank(axis=1), axis=1).where(mask.sum(axis=1) >= 3)
        per_asset = {}
        for symbol in panel.symbols:
            coverage = float(counts[symbol] / len(x)) if len(x) else 0.0
            value = finite(rank_ic[symbol])
            per_asset[symbol] = {"ic": finite(ic[symbol]), "rank_ic": value,
                                 "rank_ic_ir": finite(ir.get(symbol)),
                                 "samples": int(counts[symbol]), "nan_fraction": 1 - coverage}
            prefix = f"h{horizon}/{symbol}"
            report["checks"][f"{prefix}/samples"] = int(counts[symbol]) >= protocol.min_samples
            report["checks"][f"{prefix}/coverage"] = 1 - coverage <= protocol.max_nan_fraction
            report["checks"][f"{prefix}/rank_ic"] = value is not None and value >= protocol.min_rank_ic
        report["metrics"][str(horizon)] = {"per_asset": per_asset,
                                           "cross_section_rank_ic": finite(cross.mean()),
                                           "cross_section_samples": int(cross.count())}
    report["passed"] = all(report["checks"].values())
    return report


def retention_checks(train: dict, valid: dict, protocol: ResearchProtocol) -> dict:
    checks = {}
    for h, metrics in train["metrics"].items():
        for symbol, first in metrics["per_asset"].items():
            a, b = first["rank_ic"], valid["metrics"][h]["per_asset"][symbol]["rank_ic"]
            checks[f"h{h}/{symbol}/retention"] = (
                a is not None and b is not None and a > 0 and b >= a * protocol.min_valid_retention)
    return checks


def redundancy(panel: MarketPanel, spec: FactorSpec, library: dict, protocol: ResearchProtocol) -> dict:
    expression = Expression.parse(spec.expression, panel.fields)
    for name, entry in library.items():
        other = FactorSpec.model_validate(entry["spec"])
        if Expression.parse(other.expression, panel.fields).structure == expression.structure:
            return {"redundant": True, "tier": "structural", "factor": name, "correlation": None}
    candidate = factor_values(panel, spec)
    best, match = 0.0, None
    for name, entry in library.items():
        other = factor_values(panel, FactorSpec.model_validate(entry["spec"]))
        # Require meaningful overlap; undefined correlation is not evidence of novelty.
        overlap = (candidate.notna() & other.notna()).sum()
        corr = candidate.corrwith(other).abs().where(overlap >= protocol.min_samples)
        maximum = finite(corr.max())
        if maximum is None:
            return {"redundant": True, "tier": "insufficient_overlap", "factor": name,
                    "correlation": None}
        if maximum > best:
            best, match = maximum, name
    return {"redundant": best > protocol.max_correlation, "tier": "numeric",
            "factor": match, "correlation": best}


def _performance(returns: pd.Series, annual: float) -> dict:
    values = returns.dropna()
    if not len(values):
        return dict(annual_return=None, sharpe=None, sortino=None, max_drawdown=None,
                    calmar=None, total_return=None, samples=0)
    # Include initial capital in the peak; otherwise the very first loss disappears.
    equity = (1 + values).cumprod()
    peak = equity.cummax().clip(lower=1)
    drawdown = float((1 - equity / peak).max())
    terminal = float(equity.iloc[-1])
    growth = math.log(terminal) * annual / len(values) if terminal > 0 else None
    arr = finite(math.expm1(growth)) if growth is not None and growth < 700 else None
    std = values.std()
    downside = np.sqrt((values.clip(upper=0) ** 2).mean())
    return {"annual_return": arr, "sharpe": finite(values.mean() / std * math.sqrt(annual)) if std > 0 else None,
            "sortino": finite(values.mean() / downside * math.sqrt(annual)) if downside > 0 else None,
            "max_drawdown": drawdown, "calmar": finite(arr / drawdown) if arr is not None and drawdown > 0 else None,
            "total_return": finite(terminal - 1), "samples": len(values)}


def strategy_backtest(panel: MarketPanel, spec: StrategySpec, library: dict,
                      protocol: ResearchProtocol) -> tuple[dict, pd.DataFrame]:
    if not library:
        raise ValueError("strategy requires admitted factors")
    expression = Expression.parse(spec.expression, library)
    factors = {name: factor_values(panel, FactorSpec.model_validate(library[name]["spec"]))
               for name in expression.names}
    values = expression.evaluate(factors)
    if spec.position_rule == "rank":
        rank = values.rank(axis=1, method="average", pct=True)
        target = rank if spec.long_only else rank.sub(rank.mean(axis=1), axis=0)
    elif spec.position_rule == "threshold":
        target = np.sign(values) * (values.abs() > spec.threshold)
    else:
        target = values.clip(-1, 1)
    if spec.long_only:
        target = target.clip(lower=0)
    target = target.fillna(0)
    target = target.div(target.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    opening = panel.fields["open"]
    forward = opening.shift(-1) / opening - 1
    # Scores from the prior completed bar only. Last bar has no exit price.
    weights = target.shift(1).fillna(0).iloc[:-1]
    forward = forward.iloc[:-1]
    cost = (protocol.cost_bps + protocol.slippage_bps) / 10_000
    if spec.engine == "stateful":
        # A bounded policy, not arbitrary Python: stop after a close-observed loss;
        # exit at the next open and remain flat for cooldown bars. Loop over bars only.
        cooldown = np.zeros(len(panel.symbols), dtype=int)
        entry = np.zeros(len(panel.symbols))
        previous = np.zeros(len(panel.symbols))
        price = opening.to_numpy()
        closes = panel.fields["close"].to_numpy()
        matrix = weights.to_numpy(copy=True)
        for i in range(len(matrix)):
            if i:
                signed_return = (closes[i - 1] / np.where(entry > 0, entry, np.nan) - 1) * np.sign(previous)
                stopped = (previous != 0) & (signed_return <= -spec.stop_loss)
                cooldown[stopped] = spec.cooldown
            matrix[i, cooldown > 0] = 0
            opened = (matrix[i] != 0) & (np.sign(matrix[i]) != np.sign(previous))
            entry[opened] = price[i, opened]
            entry[matrix[i] == 0] = 0
            previous = matrix[i].copy()
            cooldown = np.maximum(cooldown - 1, 0)
        weights = pd.DataFrame(matrix, index=weights.index, columns=weights.columns)
    # Compare targets with the previous portfolio after price drift. Differencing
    # targets alone undercharges a constant-weight strategy that must rebalance.
    gross_pnl = (weights * forward).mask(weights.eq(0), 0)
    gross_portfolio = gross_pnl.sum(axis=1, min_count=len(panel.symbols))
    drifted = weights.mul(1 + forward).mask(weights.eq(0), 0).div(1 + gross_portfolio, axis=0)
    turnover = (weights - drifted.shift(1).fillna(0)).abs()
    if len(weights):
        turnover.iloc[0] = weights.iloc[0].abs()
        turnover.iloc[-1] += drifted.iloc[-1].abs()  # final liquidation is charged
    missing_held = (weights.ne(0) & forward.isna()).any().any()
    pnl = weights * forward
    pnl = pnl.mask(weights.eq(0), 0) - turnover * cost
    portfolio = pnl.sum(axis=1, min_count=len(panel.symbols))
    report = {"kind": "strategy", "strategy": spec.model_dump(),
              "used_factors": sorted(expression.names),
              "execution": "close signal -> next open; open-to-open return; gross exposure <= 1; drift-adjusted pre-fee target turnover",
              "portfolio": _performance(portfolio, protocol.periods_per_year),
              "per_asset": {s: _performance(pnl[s], protocol.periods_per_year) for s in panel.symbols},
              "turnover": float(turnover.sum().sum()),
              "cost": float(turnover.sum().sum() * cost),
              "checks": {"held_prices_available": not bool(missing_held),
                         "finite_returns": bool(np.isfinite(portfolio.to_numpy()).all()),
                         "solvent": bool((portfolio > -1).all())},
              "factor_diagnostics": {name: factor_report(panel, FactorSpec.model_validate(library[name]["spec"]), protocol)
                                     for name in expression.names}}
    for scope, metrics in {"portfolio": report["portfolio"], **report["per_asset"]}.items():
        for metric, minimum in (("sharpe", protocol.min_sharpe), ("annual_return", protocol.min_annual_return)):
            value = metrics[metric]
            report["checks"][f"{scope}/{metric}"] = value is not None and value >= minimum
        report["checks"][f"{scope}/drawdown"] = metrics["max_drawdown"] is not None and metrics["max_drawdown"] <= protocol.max_drawdown
        report["checks"][f"{scope}/samples"] = metrics["samples"] >= protocol.min_samples
    report["passed"] = all(report["checks"].values())
    curve = pd.DataFrame({"return": portfolio, "equity": (1 + portfolio).cumprod(),
                          "turnover": turnover.sum(axis=1)})
    return report, curve
