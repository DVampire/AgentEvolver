---
name: benchmark
description: "Provides versioned benchmark definitions and the manager used to load and execute them. Built-in adapters live in `default/`; `harbor/` runs Harbor-hosted benchmarks and lets Harbor score them."
version: 1.0.0
type: module
category: benchmark
requirements: []
metadata: {}
---
# Benchmark

Versioned benchmark definitions and the manager that loads and runs them.

| File | Responsibility |
|---|---|
| `types.py` | `Benchmark`, `Task`, `Stats` contracts |
| `context.py` | Benchmark registry and lifecycle state |
| `server.py` | Public `benchmark_manager` facade |
| `utils.py` | `ensure_dataset` and shared helpers |
| `default/` | Built-in adapters, registered with `BENCHMARK` |
| `harbor/` | Runs our agents on Harbor task sets, scored by Harbor |

New benchmarks implement the base contract and register through the registry rather than
adding selection logic to the server.

## Supported benchmarks

| Name | Measures | Instances | Data | Scored by | Entry point |
|---|---|---:|---|---|---|
| `aime24` | Competition maths (AIME 2024) | 30 | `Maxwell-Jia/AIME_2024` | exact match, or LLM judge | in-process |
| `aime25` | Competition maths (AIME 2025) | 30 | `opencompass/AIME2025` | exact match, or LLM judge | in-process |
| `gpqa` | Graduate-level science MCQ | 448 | `Idavidrein/gpqa` — gated | exact match, or LLM judge | in-process |
| `gsm8k` | Grade-school word problems | 1319 | `openai/gsm8k` | exact match, or LLM judge | in-process |
| `hle` | Humanity's Last Exam, multi-modal | 2500 | `cais/hle` | exact match, or LLM judge | `examples/run_hle.py` |
| `deepweb` | Deep-research over the live web | 100 | ships with the repo | exact match, or LLM judge | in-process |
| `leetcode` | Programming problems, resumable | — | supplied locally | hidden tests, in a subprocess | in-process |
| `programbench` | Rebuild a codebase from its binary | 201 | `programbench/ProgramBench-Tests` | official `programbench eval`, per-branch tests in Docker | `examples/run_programbench.py` |
| `swebench_verified` | Resolve a real GitHub issue (Python) | 500 | `SWE-bench/SWE-bench_Verified` | hidden `fail_to_pass`/`pass_to_pass`, graded on the host | `examples/run_swebench_verified.py` |
| `swebench_pro` | Resolve a real issue (Python/Go/JS/TS) | 731 | `ScaleAI/SWE-bench_Pro` | same, via the official Pro grader | `examples/run_swebench_pro.py` |
| `exact_match` | Nothing of its own — scores answers it is handed | — | none | numeric-tolerant exact match | in-process |
| *(any Harbor task set)* | e.g. `deep-swe`, `terminal-bench` | varies | Harbor's own | **Harbor's verifier, in Harbor's container** | `harbor/` — see below |

"in-process" means the benchmark yields tasks and the framework's own runtime answers
them. The three with launchers need per-instance Docker containers and a host-side grader,
which is orchestration a `Benchmark` class deliberately does not do.

`exact_match` is a scorer, not a task source: it has no dataset and appears here only
because it is registered alongside the rest.

### Not supported, and why

| Name | Reason |
|---|---|
| `frontiercode` | Cognition does not release the tasks — they evaluate submitted models instead. No dataset, no harness, no schema to load. Listed in `datasets/load.py` so that looking for it finds this reason. |

## Harbor benchmarks

Harbor inverts the direction this module is built for: it owns the run and calls an agent,
building the task container and afterwards running the task's own verifier inside it.
`harbor/` takes that side of the deal, which is what keeps a score comparable — see
[`harbor/README.md`](harbor/README.md).

```bash
pip install 'agentevolver[harbor]'
harbor run -d "deep-swe@1.1" --agent agentevolver.benchmark.harbor:AgentEvolverAgent
```

One adapter reaches every Harbor task set, so a new Harbor benchmark needs no work here.

## Data comes from `datasets/` first

Every benchmark stores its data under `datasets/<name>/`. Declare an `hf_repo_id` field and
call `ensure_dataset(<name>, self.hf_repo_id)` (in `utils.py`) from `initialize()` before
loading: a missing or empty `datasets/<name>/` is snapshot-downloaded from HuggingFace, and
otherwise the local copy is used untouched. Both `hf_repo_id` and `path` stay
config-overridable, and `HF_ENDPOINT` selects a mirror.

Downloading first and caching after would work on a connected machine and fail on the
cluster this runs on, which is why the order is fixed rather than a preference.

`datasets/load.py` reaches the same store directly — fetch before a run rather than
during one, and see what is on disk:

```bash
python datasets/load.py --list
python datasets/load.py swebench_verified
```

It reads each dataset's location from the benchmark class itself. Stating those in two
places is how that index once named three HuggingFace repos no benchmark had ever used.

## Scoring caveats worth carrying

Two of these expose the grader to the agent as a progress signal (`swebench_*` via
`bridge.py`, `programbench` likewise). Iterating against a signal a single-shot run does
not have means such a score is **not comparable to a public leaderboard**; the launchers
record how many times the tool was called so the caveat travels with the number.

The same applies to Harbor for a different reason: a leaderboard row is a specific agent
harness on a specific model, and swapping the harness changes what is measured even when
the model is identical.
