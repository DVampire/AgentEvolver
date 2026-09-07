"""Adversarial expressions and hand-calculated return paths for the research engine."""

import numpy as np
import pandas as pd
import pytest

from agentevolver.data.factor_mining import FactorMarketDataset, MarketPanel
from agentevolver.environment.default.factor_mining.expressions import Expression
from agentevolver.environment.default.factor_mining.research import (
    FactorSpec,
    ResearchProtocol,
    StrategySpec,
    factor_report,
    redundancy,
    strategy_backtest,
)


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo unsafe')", "close.__class__", "close.iloc[-1]",
    "[x for x in close]", "delay(close, -1)", "delta(close, 0)",
    "ts_mean(close, 2 + 1)", "future_return", "close ** 10000", "close ** close",
    "ts_mean(ts_mean(close, 200), 200)", "True * close", "ts_mean(close, window=3)",
])
def test_expression_cannot_escape_or_look_ahead(expression):
    with pytest.raises(ValueError):
        Expression.parse(expression, ["close"])


def test_expression_causality_and_structure():
    panel = FactorMarketDataset.synthetic(bars=300, assets=3)
    expression = Expression.parse("rank(delta(close, 5))", panel.fields)
    before = expression.evaluate(panel.fields)
    panel.fields["close"].iloc[200:] *= 2
    after = expression.evaluate(panel.fields)
    pd.testing.assert_frame_equal(before.iloc[:200], after.iloc[:200])
    assert expression.structure == Expression.parse("rank(delta(close, 20))", panel.fields).structure
    assert expression.paradigm == "cross_section"


def test_factor_is_checked_on_every_asset_and_missing_data_fails():
    panel = FactorMarketDataset.synthetic(bars=500, assets=3)
    factor = FactorSpec(name="momentum", expression="delta(close, 1) / delay(close, 1)")
    protocol = ResearchProtocol(horizons=[1])
    report = factor_report(panel, factor, protocol)
    assert report["passed"]
    assert set(report["metrics"]["1"]["per_asset"]) == set(panel.symbols)
    panel.fields["close"].loc[:, "SYNTH1"] = np.nan
    report = factor_report(panel, factor, protocol)
    assert not report["passed"]
    assert not report["checks"]["h1/SYNTH1/coverage"]


def test_structural_and_numeric_duplicates_are_rejected():
    panel = FactorMarketDataset.synthetic(bars=400, assets=3)
    library = {"price": {"spec": FactorSpec(name="price", expression="close").model_dump()}}
    protocol = ResearchProtocol()
    assert redundancy(panel, FactorSpec(name="same", expression="close"), library, protocol)["tier"] == "structural"
    duplicate = redundancy(panel, FactorSpec(name="scaled", expression="2 * close"), library, protocol)
    assert duplicate["redundant"] and duplicate["correlation"] == pytest.approx(1)


def tiny_panel():
    # At close[0] both scores positive: next-open positions are 0.5, 0.5.
    # open[1] -> open[2] returns +20%, -10%, so portfolio gross return is 5%.
    index = pd.date_range("2020-01-01", periods=4, freq="h", tz="UTC")
    opening = pd.DataFrame({"A": [100., 110., 132., 132.], "B": [100., 100., 90., 90.]}, index=index)
    return MarketPanel({"open": opening, "close": opening.copy()}, "1h")


def test_next_open_execution_and_costs_are_hand_calculable():
    panel = tiny_panel()
    library = {"price": {"spec": FactorSpec(name="price", expression="close").model_dump()}}
    spec = StrategySpec(name="hold", expression="price", position_rule="threshold", long_only=True)
    report, curve = strategy_backtest(panel, spec, library,
                                      ResearchProtocol(cost_bps=10, slippage_bps=0))
    assert curve["return"].iloc[0] == 0  # no same-bar fill
    assert curve["return"].iloc[1] == pytest.approx(.05 - .001)
    rebalance = abs(.5 - .6 / 1.05) + abs(.5 - .45 / 1.05)
    assert curve["return"].iloc[2] == pytest.approx(-.001 * (1 + rebalance))
    assert report["cost"] == pytest.approx(.001 * (2 + rebalance))
    assert report["turnover"] == pytest.approx(2 + rebalance)
    assert curve.equity.iloc[-1] == pytest.approx(1.049 * (1 - .001 * (1 + rebalance)))


def test_missing_held_prices_are_not_a_free_zero_return():
    panel = tiny_panel()
    panel.fields["open"].iloc[2, 0] = np.nan
    library = {"price": {"spec": FactorSpec(name="price", expression="close").model_dump()}}
    spec = StrategySpec(name="hold", expression="price", position_rule="threshold")
    report, _ = strategy_backtest(panel, spec, library, ResearchProtocol())
    assert not report["checks"]["held_prices_available"]
    assert not report["passed"]


def test_strategy_cannot_inline_an_unadmitted_raw_factor():
    panel = FactorMarketDataset.synthetic(bars=150, assets=2)
    library = {"momentum": {"spec": FactorSpec(name="momentum", expression="delta(close, 1)").model_dump()}}
    with pytest.raises(ValueError):
        strategy_backtest(panel, StrategySpec(name="bypass", expression="close * momentum"),
                          library, ResearchProtocol())


def test_stateful_stop_is_observed_at_close_and_exits_next_open():
    panel = tiny_panel()
    panel.fields["close"].iloc[1] = [88, 80]  # loss observed AFTER entry at open[1]
    library = {"price": {"spec": FactorSpec(name="price", expression="close").model_dump()}}
    spec = StrategySpec(name="stop", expression="price", position_rule="threshold",
                        engine="stateful", stop_loss=.1, cooldown=2)
    _, curve = strategy_backtest(panel, spec, library, ResearchProtocol(cost_bps=0, slippage_bps=0))
    assert curve["return"].iloc[1] == pytest.approx(.05)  # not retroactively removed
    assert curve["return"].iloc[2] == 0


def test_initial_loss_is_part_of_drawdown():
    from agentevolver.environment.default.factor_mining.research import _performance

    metrics = _performance(pd.Series([-.2, .1]), 2)
    assert metrics["max_drawdown"] == pytest.approx(.2)
