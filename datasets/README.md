# Datasets

Where the benchmarks in `agentevolver/benchmark/default/` read their data.

Each one resolves through `ensure_dataset`, which downloads into `datasets/<name>/` on
first use. That makes a run self-healing, but it also means the data is invisible until
something runs. `load.py` is the direct way to the same store:

```bash
python datasets/load.py --list                      # what is on disk, and how much
python datasets/load.py swebench_verified           # fetch one, before a run needs it
python datasets/load.py --all                       # fetch everything fetchable
```

Downloads are idempotent — a populated dataset is reported and left alone.

## What is here

| Benchmark | Directory | Instances | Source |
|---|---|---|---|
| `aime24` | `AIME24/` | 30 | `HuggingFaceH4/aime_2024` |
| `aime25` | `AIME25/` | 30 | `yentinglin/aime_2025` |
| `gpqa` | `GPQA/` | 448 | `Idavidrein/gpqa` — gated, needs `HF_TOKEN` |
| `gsm8k` | `gsm8k/` | 1319 | `openai/gsm8k` (config `main`) |
| `hle` | `hle/` | 2500 | `cais/hle` |
| `deepweb` | `deepweb-bench/` | 100 | ships with the repository |
| `programbench` | `ProgramBench-Tests/` | 201 | `programbench/ProgramBench-Tests` (~8 GB) |
| `swebench_verified` | `SWE-bench_Verified/` | 500 | `SWE-bench/SWE-bench_Verified` |
| `swebench_pro` | `SWE-bench_Pro/` | 731 | `ScaleAI/SWE-bench_Pro` |

`load.py --list` compares what it finds against these counts and says so when they
disagree, because a short split is what a partial download looks like — and it would
otherwise surface much later as a benchmark quietly scoring fewer instances than the
number it is being compared against.

## FrontierCode

Not here, and not fetchable. Cognition states they "don't currently plan to release the
tasks publicly to avoid contamination", and evaluate submitted models themselves; Epoch
AI's page sources its numbers from Cognition's leaderboard rather than running the set.
There is no dataset, no harness, and no schema to write a loader against. It is listed in
`load.py` so that looking for it finds this reason rather than nothing.

## Ownership

Some directories here are owned by `root` from an earlier import, and a download into one
fails with `PermissionError`. `load.py` reports the dataset as missing, which is what it
is. Fixing it takes `chown`, which is a decision for whoever administers the machine.
