# Datasets

Local dataset files used by benchmarks. Downloading, checking, parsing and repair
belong to [`agentevolver/data`](../agentevolver/data/README.md); this directory
contains no Python entry point. Data-backed benchmarks construct
their dataset class: existing local files are reused; missing snapshots are
downloaded into the same directory before loading:

```python
from agentevolver.data import SWEBenchVerifiedDataset

dataset = SWEBenchVerifiedDataset()
check = SWEBenchVerifiedDataset.inspect()
```

Downloads are idempotent — a populated dataset is reported and left alone. That skip is
deliberate (fetching first and caching after fails on the cluster this runs on), but it
means a download interrupted partway never heals itself: the directory reads as present
forever. The class's `download(repair=True)` re-checks its source and fills in missing files. It is
idempotent in result but not in transfer: files are skipped only when the cache metadata
beside them can vouch for them, so a directory populated by other means is fetched again.

```python
from agentevolver.data import DeepWebDataset

DeepWebDataset.download(repair=True)
```

## What is here

| Benchmark | Directory | Instances | Source |
|---|---|---|---|
| `aime24` | `AIME24/` | 30 | `Maxwell-Jia/AIME_2024` |
| `aime25` | `AIME25/` | 30 | `opencompass/AIME2025` |
| `gpqa` | `GPQA/` | 448 (main subset) | `Idavidrein/gpqa` — gated, needs `HF_TOKEN` |
| `gsm8k` | `gsm8k/` | 1319 | `openai/gsm8k` (config `main`) |
| `hle` | `hle/` | 2500 | `cais/hle` — gated, needs `HF_TOKEN` |
| `deepweb` | `deepweb-bench/` | 100 | `deepweb-bench-anon/deepweb-bench` |
| `programbench` | `ProgramBench-Tests/` | 201 | `programbench/ProgramBench-Tests` (~8 GB) |
| `swebench_verified` | `SWE-bench_Verified/` | 500 | `SWE-bench/SWE-bench_Verified` |
| `swebench_pro` | `SWE-bench_Pro/` | 731 | `ScaleAI/SWE-bench_Pro` |

Each class's `inspect()` returns local presence, parsed count, expected count for
the selected subset/split, and parsing errors. Callers can compare these before
running a benchmark so a partial dataset is not silently treated as complete.

## FrontierCode

Not here, and not fetchable. Cognition states they "don't currently plan to release the
tasks publicly to avoid contamination", and evaluate submitted models themselves; Epoch
AI's page sources its numbers from Cognition's leaderboard rather than running the set.
There is no dataset, no harness, and no schema to write a loader against. It has no registered data class.

## Gated datasets

`gpqa` and `hle` are gated on HuggingFace: both need `HF_TOKEN` in `.env` **and** access
granted to that token's account on the dataset page. Without it the download fails;
the data class's `inspect()` still reports the local state without downloading.
