# Factor research benchmark

This package holds the independent numerical factor/strategy benchmark and its calculation
utilities. It does not register an Environment or start research agents. The former fixed
factor environment and two-worker experiment have been removed; use the
[Signal Foundry demo](../../../../docs/demos/factor_strategy_mining.md) for the single agent
that develops its own connector and research environments.

| File | Responsibility |
| --- | --- |
| `__init__.py` | BenchmarkManager integration, bounded validation/admission, frozen submission and final-test grading |
| `expressions.py` | Causal expression interpreter and bounded AST/operator vocabulary |
| `research.py` | Factor/strategy contracts, metrics, numerical backtests and reports |
| `bridge.py` | File-based client for benchmark-owned validation requests |
| `agentevolver/data/factor_mining.py` | OHLCV import, panel alignment, split bundles and fingerprints |

Use `FactorMarketDataset.load` for CSV/Parquet or `from_dataset` for a DataManager source,
then `FactorMarketDataset.bundle` to create disjoint train/valid/test files. Inputs include
`timestamp, symbol, open, high, low, close, volume`. Missing observations remain missing.
`frequency="observed"` follows supplied timestamps; it does not verify exchange calendars.
Synthetic panels are test fixtures and provide no evidence of profitable market signals.

Use the usual BenchmarkManager `configure/reset/prepare/submit/eval/stats/cleanup` methods
with benchmark name `factor_mining`. Preparation copies only training observations to the
workspace and creates a validation bridge. Validation has its own persistent quota and
admission ledger. Final submission freezes the answer and closes validation; final-test
results can be read again but cannot be used to revise that submission. The caller must
provide process/filesystem isolation if its evaluator requires an enforced private holdout.

The numerical model uses causal close-derived signals, next-open execution, turnover costs
and terminal liquidation. It is a multi-asset reference engine. It does not implement the
new stock study's full corporate-action accounting, point-in-time revisions, financing or
market impact; using these utilities does not establish those capabilities.

Run the numerical and evaluator regressions without model calls:

```bash
python -m pytest tests/test_factor_market_data.py tests/test_factor_research.py tests/test_factor_mining_benchmark.py
```
