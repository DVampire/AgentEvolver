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

## Manager-only public interface

Applications import `benchmark_manager`, `Task` and result/metadata types. They never
import, construct or receive a concrete benchmark implementation. All 11 built-ins are
registered internally; only the manager/context own their instances.

## Run scripts and evaluation boundary

`examples/run_hle.py` demonstrates the division of responsibility. Each
`examples/run_*.py` owns its CLI/configuration, agent and runtime initialization,
task scheduling, process/container execution, monitoring and shutdown. Coding examples
retain their `run_inner`, `run_launcher` and `main` functions. The manager does not
launch agents or dispatch an entire experiment; there is no `benchmark_manager.run()`.

`Benchmark` owns the task lifecycle. Applications use the manager's uniform
`prepare → submit → eval → cleanup` methods. The base class owns submission identity,
freezing, receipt validation, durable result records and resource cleanup. Concrete
classes implement private hooks; they do not add public entry points.

`SWEBenchBenchmark` owns repository preparation and patch collection for Pro and
Verified. Their image/workspace settings differ: Pro seeds `/app` into its task mount,
while Verified uses `/testbed`. `ProgramBenchmark` owns binary protection and archive
packaging. Answer benchmarks use the base class's lightweight preparation and answer
submission. HLE adds image attachments. LeetCode starts its grading browser lazily.
There is no separate facilities module or benchmark runner.

```python
# Config, Agent initialization and concurrency belong to the run script.
task = await benchmark_manager.reset(name)
try:
    while task is not None:
        context = await benchmark_manager.prepare(name, task)
        try:
            if not context.completed:
                output = None
                if context.submission is None:
                    output = await solve_with_agent(context)  # Defined by the example.
                await benchmark_manager.submit(name, task, output=output)
                await benchmark_manager.eval(name, task)
        finally:
            await benchmark_manager.cleanup(name, task)
        task = await benchmark_manager.step(name)
    stats = await benchmark_manager.stats(name)
finally:
    await benchmark_manager.cleanup(name)
```

| Manager method | Return | Contract |
|---|---|---|
| `configure(name, **settings)` | `BenchmarkInfo` | Configure an owned instance. `base_dir` identifies the run's benchmark state; `resume=True` restores its evaluation ledger. |
| `initialize(benchmark_names=[...])` | `None` | Load named benchmarks without consuming a task. |
| `reset(name, split=None, resume=False)` | `Task \| None` | Rewind and return the first task. `resume=True` retains restored statistics; active task contexts must be cleaned up first. |
| `step(name)` | `Task \| None` | Fetch the next task; never allocate task containers or grade it. |
| `prepare(name, task, context=...)` | `BenchmarkTaskContext` | Prepare one selected task or recover its submission. Returns data: paths, payload, container identity and recovery state. |
| `submit(name, task, output=...)` | `Task` | Stop writable task resources, collect the artifact once and freeze it under a process-safe transaction lock. |
| `eval(name, task)` | `Task` | Validate the frozen artifact, run the grader and persist the final result. Also accepts existing externally frozen coding submissions or direct answer tasks. |
| `llm_judge(name, task)` | `float` | Explicit answer judging; does not record a result or replace coding graders. |
| `stats(name)` | `Stats` | Scores plus `extra.completed_task_ids`, persisted evaluation snapshots and recorded usage. |
| `cleanup(name, task, reclaim=False)` | `None` | Release a single task, preserving frozen submissions and evidence. SWE disk reclamation requires a final valid score. |
| `cleanup(name)` / `cleanup()` | `None` | Release the selected benchmark or all managed resources, including shared mounts after workers finish. |
| `get_info(name, evaluation_options=..., expected_evaluation=...)` | `BenchmarkInfo \| None` | Detached metadata, submission protocol, grader identity and compatibility validation. |
| `task_payload(name, record)` | `dict` | Project solver-visible input without exposing the answer key. |
| `catalog()` / `is_loaded(name)` | metadata / `bool` | Discover registered benchmarks or check initialization. |

Preparation context accepts `session_dir`, `workspace_dir`, `output_dir`, `resume`,
`agent_on_host`, runtime `mounts`/`env`, `writable_paths` and a timeout. Runtime choices
come from the example; dataset image paths, task mounts and permission rules come from
the benchmark. The worker binds the returned directory to its own Agent context.
Agent processes must finish before `submit`; the benchmark then stops its writable
container before collecting the final artifact. No Agent is started by these methods.

Each run stores `evaluations.json` under its benchmark `base_dir`. Task submissions
and receipts live in their session directories. SWE and ProgramBench accept a configured
`history_path` when resuming legacy result JSON; importing it does not rewrite that file.
Passed and failed verdicts are both terminal. Evaluation errors retry the same frozen
submission; a missing/tampered receipt or artifact must never start another solver.
Use a new run directory for a new attempt. Existing patch/archive formats remain readable.

Atomic files reuse `utils.file_utils`; trace-based cost summaries reuse the canonical
TokenUsage reducer in `trace.stats`. These are internal dependencies, not extra manager
business methods. Experiment display/export remains in the examples and visual module.

`configure` is followed by `initialize` or a lazy data operation (`reset`, `step`, `eval`,
`stats`). Call `reset`, reconfiguration and `cleanup` after outstanding evaluations finish.
A task starts with `score=None`; `eval` returns `passed`, `failed` or `error` in
`Task.evaluation`. Errors stay unscored, cancellation propagates, and official graders
retain their own verdict rules.

`Stats.total` is dataset size (`exact_match` uses evaluated task count). `attempted`
counts distinct recorded task IDs (including preparation/submission errors), `scored = correct + wrong`, and `errors = attempted - scored`.
`accuracy = correct / scored` counts fully correct tasks; `mean_score` includes partial
credit. Both exclude errors and are zero before any valid score. A retry replaces its
statistics entry; editing a returned Task cannot change the saved snapshot. These are
evaluator statistics, distinct from the monitor's completed-attempt pass rate.

The manager's former `get()` is removed. `get_info`, `configure`, `register`, `update`
and `restore` return `BenchmarkInfo`, which has no `cls`, `instance` or executable code.
Registration/version APIs are for defining extensions, not acquiring runtime instances.
The package no longer re-exports built-in classes. `datasets/load.py` uses `catalog()`.

SWE Pro computes its grader fingerprint internally. A launcher requests evaluation
metadata from `get_info`; on resume it passes the saved identity back to the same API.
The manager rejects changed profiles, implementations and grader assets. Launchers store
that returned metadata without importing fingerprint functions or implementing comparisons.
SWE oracle rows remain inside the evaluator; `task_payload` centrally projects inputs.
LeetCode batches concurrent `eval` calls internally and finishes partial batches without
requiring a caller-side queue or flush endpoint.

Inside this module, `Benchmark` supplies common wrappers. Implementations provide private
hooks `_initialize`, `_step`, `_eval`, `_stats`, optionally `_reset`, `_cleanup`,
`_evaluation_info` and the pure `_task_payload`. Extra public implementation methods and
wrapper overrides are rejected at class definition. Private grader/parser helpers stay
inside the module. Contract tests audit production imports and manager access; unit tests
may inspect implementations to test the official grader and adapter internals.

```python
from agentevolver.benchmark import benchmark_manager

await benchmark_manager.configure("gsm8k")
try:
    task = await benchmark_manager.reset("gsm8k")
    while task is not None:
        task.result = await solve(task)
        evaluated = await benchmark_manager.eval("gsm8k", task)
        task = await benchmark_manager.step("gsm8k")
    summary = await benchmark_manager.stats("gsm8k")
finally:
    await benchmark_manager.cleanup()
```

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
with scheduling owned by the launcher and final grading owned by the Benchmark implementation.

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
not a certified leaderboard score. Legacy/diagnostic evaluation issues remain unscored, not silently
converted to assertion failures: `test_compatibility` flags compilation/interface failures
whose attribution needs review, `grading_setup` covers missing fixtures/parsing/selection,
and `evaluation` covers other unresolved execution issues. Legacy `test_build_failed`
records receive the same display classification without rewriting their original ledger.
Missing-fixture mentions in failed test logs are diagnostic hints only: negative tests
can intentionally reference absent paths, so these hints never repair fixtures or change
official grading. Test builds are not automatically treated as host infrastructure faults.

SWE Pro's default `official` grading is implemented inside `default/swebench.py`,
ported from the upstream local-Docker evaluator at commit
`ca10a60a5fcae51e6948ffe1485d4153d421e6c5`. It uses upstream image naming, the Docker
SDK container invocation, unmodified run/parser scripts, and upstream set-membership
scoring. Compilation messages, skipped inner tests and nonzero container exit codes do
not override the parser's official result. Missing evaluator output scores false, as in
upstream main. Evidence remains beside the session under `evaluation/official-*`.
No separate CLI scheduler or alternate result ledger is needed. Install the `benchmark`
extra to include the Docker SDK. The explicit `diagnostic` profile retains local repairs
and stricter checks; its results are not leaderboard-comparable. Run state records the
grader implementation so a resume cannot silently mix old and migrated grading.

SWE Pro solver image pulls and workspace seeding use cancellable subprocesses, not executor
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
call `ensure_dataset(<name>, self.hf_repo_id)` (in `utils.py`) from `_initialize()` before
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

It reads each dataset's location through `benchmark_manager.catalog()`. Stating those in two
places is how that index once named three HuggingFace repos no benchmark had ever used.

## Scoring caveats worth carrying

All three coding launchers call `benchmark_manager.eval(name, task)` after the solver
container has stopped and its submission has been frozen. The Benchmark owns the grader,
result parsing and evidence. There are no model-facing benchmark evaluation tools, request
bridges, hidden-test feedback loops, or grading budgets. Local checks remain agent tools.
Historical feedback-assisted scores remain preserved and are not strict pass@1.

`Task.evaluation` carries a structured `EvaluationResult` (`passed`, `failed`, or `error`).
An evaluation error has `score=None`, never an implicit zero. Batch evaluation raises on
unscored errors rather than silently averaging them as wrong answers. Registry/lifecycle
management remains in context/server; benchmark-specific grading stays in its implementation.

SWE Pro defaults to `--grader-profile official`: upstream run scripts and parsers are
uploaded unchanged, and entry-script generation is regression-tested against the local
upstream generator. It does not restore extra fixtures or rewrite selectors. The local
grader asset fingerprint and profile are recorded and checked on resume. This is asset
compatibility, not a claim that the entire custom launcher has been certified by a leaderboard.

`--grader-profile diagnostic` explicitly enables bounded test-worker parallelism,
fail-fast setup, selector/parser repairs and Go test-data revision matching.
All diagnostic results are marked `leaderboard_comparable=false`, even without restored
files. New logs and parser output are retained in `evaluation/grader-*.{log,json}`;
historical `eval_bridge/` evidence remains untouched. A compiler warning alone,
or an expected missing-file error in an executed test, is not a harness failure.

For Go tests, the diagnostic grader restores added/modified non-code data files under the
selected tests' sibling `testdata/` directories from the injected tests' revision. This can
replace existing test fixtures, but never restores production code, executable files or
symlinks. Restored paths stay in the grader evidence; the result
contains only `fixture_files_restored` and `leaderboard_comparable=false`. This repair
changes the grading setup and must not be presented as an unmodified official evaluation.
Already-running launchers keep their loaded protocol until restarted; editing code does
not silently rewrite previous results.

Host-only controls are available with `python -m others.swe_grader_audit --help`.
The audit evaluates either the dataset reference or an existing frozen submission in a
separate grading namespace; it never calls an LLM, feeds test evidence back to a solver,
or changes benchmark scores. `--without-reference-fixtures` isolates test-data dependence
while retaining the reference production code. A passing reference alone does not prove
fairness: hidden tests can still depend on undocumented private implementation details.
Keep official grades and corrected diagnostic grades distinguishable, and retry grading
the same frozen patch for infrastructure failures rather than generating a new answer.

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
