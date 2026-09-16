---
name: benchmark_default
description: "Registers the built-in benchmark adapters — AIME, GPQA, GSM8K, HLE, DeepWeb, LeetCode, ProgramBench and both SWE-bench sets. Implementations conform to the contracts documented by the parent Benchmark module."
version: 1.0.0
type: collection
category: benchmark
requirements: []
metadata: {}
---
# Built-in benchmarks

Registers the adapters listed in the parent module's
[supported-benchmarks table](../README.md#supported-benchmarks): AIME 2024/2025, GPQA,
GSM8K, HLE, DeepWeb, LeetCode, ProgramBench, SWE-bench Verified and SWE-bench Pro, plus
`exact_match`, which scores answers it is handed rather than sourcing tasks.

Implementations conform to the contracts documented by the parent Benchmark module. The
table there is the one to update when adding an adapter — it records what each one
measures, how many instances it holds, and what actually computes its score, which differ
enough between these that a single sentence would be wrong about most of them.
