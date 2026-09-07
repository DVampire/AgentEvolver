---
name: data
description: "Contains dataset adapters used by the Benchmark module, including AIME, GPQA, GSM8K, LeetCode, HLE, DeepWeb, and ProgramBench."
version: 1.0.0
type: module
category: data
requirements: []
metadata: {}
---
# Data

Contains dataset adapters used by the Benchmark module, including AIME, GPQA, GSM8K,
LeetCode, HLE, DeepWeb, and ProgramBench.

Each adapter is responsible for loading its source representation and exposing normalized
examples. Evaluation policy and benchmark execution remain in `benchmark/`; generated
outputs should not be stored in this package directory.

`factor_mining.py` provides `FactorMarketDataset` for OHLCV research assets. It
imports CSV/Parquet directly, or existing HF/local datasets through `DataManager`,
aligns timestamp × asset panels without filling missing bars, and writes disjoint
train/valid/test Parquet bundles with a content fingerprint. See
[the factor research guide](../environment/default/factor_mining/README.md).
