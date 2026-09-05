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
| `hle` | Humanity's Last Exam, multi-modal | 2500 | `cais/hle` — gated | exact match, or LLM judge | `examples/run_hle.py` |
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

Every maintained launcher publishes the same `visual.BenchmarkMonitor` state and deploys
its read-only dashboard through `deployment_manager` by default. Use `--no-monitor` for
headless automation or `--monitor-port` to request a preferred host port; port conflicts
are resolved centrally by the deploy subsystem.

To display retries on top of historical progress without changing the running solver,
place `aggregate.json` beside its `monitor.json`:
`{"history": ["/absolute/path/to/previous/results.json"], "total": 731}`.
The monitor merges by task ID (current results replace history), retains all recorded
attempt costs, and labels the view cumulative retry results, not pass@1. The original
ledgers remain untouched, so display aggregation cannot alter resume or grading behavior.

The headline pass rate is passed / completed attempts (including evaluation issues),
not passed / successfully graded attempts. The API retains the separate `scored` count
and exposes the exact numerator and denominator in `pass_rate`. In cumulative views,
each task contributes its latest result once. This is a conservative progress metric,
not a certified leaderboard score. Evaluation issues remain unscored, not silently
converted to assertion failures: `test_compatibility` flags compilation/interface failures
whose attribution needs review, `grading_setup` covers missing fixtures/parsing/selection,
and `evaluation` covers other unresolved execution issues. Legacy `test_build_failed`
records receive the same display classification without rewriting their original ledger.
Missing-fixture mentions in failed test logs are diagnostic hints only: negative tests
can intentionally reference absent paths, so these hints never repair fixtures or change
official grading. Test builds are not automatically treated as host infrastructure faults.

SWE Pro image pulls and workspace seeding use cancellable subprocesses, not executor
threads. A timed-out or cancelled seed removes its explicitly named temporary container;
copy/reset/checkout failures stop initialization instead of being hidden by a later chown.
The solver prompt asks for requirement-derived expected values and interface/type checks,
not tests that simply confirm the implementation's assumptions. This improves verification
discipline; it cannot guarantee compatibility with undocumented hidden-test interfaces.

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

SWE-bench Verified and ProgramBench expose the grader as a progress signal. Iterating against a signal a single-shot run does
not have means such a score is **not comparable to a public leaderboard**; the launchers
record how many times the tool was called so the caveat travels with the number.

SWE Pro defaults to `--grader-profile official`: upstream run scripts and parsers are
uploaded unchanged, and entry-script generation is regression-tested against the local
upstream generator. It does not restore extra fixtures or rewrite selectors. The local
grader asset fingerprint and profile are recorded and checked on resume. This is asset
compatibility, not a claim that the entire custom launcher has been certified by a leaderboard.

`--grader-profile diagnostic` explicitly enables bounded test-worker parallelism,
fail-fast setup, selector/parser repairs and optional missing Go test-data restoration.
All diagnostic results are marked `leaderboard_comparable=false`, even without restored
files. Complete logs and parser output are retained in `eval_bridge/grader-*.{log,json}`
(a historical directory name, no longer a live solver bridge). A compiler warning alone,
or an expected missing-file error in an executed test, is not a harness failure.

For Go tests, the grader can restore newly added non-code data files under the selected
tests' sibling `testdata/` directories from the test revision. It never overwrites existing
files or restores production code. Restored paths stay in the grader evidence; the result
contains only `fixture_files_restored` and `leaderboard_comparable=false`. This repair
changes the grading setup and must not be presented as an unmodified official evaluation.
Already-running launchers keep their loaded protocol until restarted; editing code does
not silently rewrite previous results.

SWE Pro now uses `swe-pro-final-only-v2`: both configurations expose only local verification
to the solver, with no hidden-grader tool, watcher or bridge environment. After the Agent
exits, its sandbox is released and one `submission.json` containing the patch, SHA-256 and
Agent outcome is frozen. The host grades that artifact, including submissions whose local
tests failed or budget ran out. Scores never flow back into the solver.

`--resume` skips both passing and failing completed scores. If grading failed or was
interrupted after submission, only that same frozen artifact is regraded; the Agent does
not run again. A separate submission receipt plus the prior result record prevents a lost
artifact from being treated as a fresh attempt; a mismatched receipt/hash also fails closed.
Interrupted pre-submission workspaces may resume without official feedback.
The former `--retry-unresolved` mode is removed. Legacy feedback-based runs and their results
are preserved, but cannot resume under the new protocol; use a new output directory and
owner. The launcher gives each run its own initially empty `output/<owner>/extension`
library and forwards it to every Agent, ignoring the global extension library; no old
generated capabilities are copied. Shipped capabilities remain available. The extension
path is locked in run state, and resume preserves that run's own evolution work. Changes to
model/scaffold settings and cross-task adaptation still need to be disclosed/audited.

The same applies to Harbor for a different reason: a leaderboard row is a specific agent
harness on a specific model, and swapping the harness changes what is measured even when
the model is identical.
