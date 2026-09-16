# Factor and factor strategy mining

Signal Foundry is a **single-agent research demo with joint factor/strategy iteration**. The agent develops its
market-data and simulation capabilities while building one continuous factor/strategy report
page. It uses the standard Agent execution loop, shared plan module, memory/compaction,
native capability router, evolution rules and deployment gateway.

The [task folder](../../examples/tasks/factor_strategy_mining/README.md) holds only the
product brief and domain study parameters. Framework implementation rules live in the
prompt/skills; runtime assembly and evidence requirements live in the config. The launcher
forwards task.html and study.json to shared task preparation without synthesizing behavior
instructions or reading an experiment.json from the task folder.

## Run

```bash
python -m examples.run_factor_strategy_mining_demo

# Use a different task folder containing task.html and study.json.
python -m examples.run_factor_strategy_mining_demo --task-dir /absolute/path/to/study
```

Only executing the launcher starts a model experiment. Default model:
`llm_hub/gpt-6-astra`; override with `--model`. The runtime ceiling is 10,000 steps,
1,000,000,000 tokens and 8 hours, with input compaction at 100,000 tokens. These ceilings
do not mandate spending the budget. The study tracks research trials and result observations without fixed search ceilings.
Runs register on the usual gateway at port 9876; deploy_tool publishes the report links.
Every launch starts a new experiment. The shared example launcher isolates its adopted
capability library under a new session namespace, alongside fresh workspace, plan and memory
state. It does not load an earlier experiment's components, candidates, plans or counters.

## Assembly and responsibilities

| Layer | Location / role |
| --- | --- |
| Agent | `agentevolver/agent/actor/factor_strategy_mining_agent.py`: thin MetaAgent specialization with child dispatch disabled |
| Prompt | `agentevolver/prompt/default/factor_strategy_mining_agent.html`: task orchestration, skill/capability routing and plan execution; includes shared runtime and evolution rules |
| Skill | `agentevolver/skill/finance/factor_strategy_research_skill/`: open exploration, expression compiler/operators, planning, data/interfaces, evaluation and single-page reports |
| Config | `configs/factor_strategy_mining_demo.py`: exactly one actor; Bash, job, inspection, adoption, deployment and completion |
| Task | `signal_foundry/task.html`: English product outline and acceptance requirements; no inline style or scripts |
| Study | `signal_foundry/study.json`: stock, dates, costs, factor qualification and research-quality requirements |
| Runtime policy | `task_manifest_defaults` in the demo config: deployment, self-review, evolution evidence and declared holdout control |

No market connector or factor/strategy environment is preloaded in this demo. The agent
lists currently loaded capabilities with `inspect_tool(capability_type=...)`, inspects
exact returned names, establishes concrete limitations and develops or improves
an appropriate Connector and **two distinct Environment components**. Environments can
share verified numerical utilities. The router makes registered environments callable via
`accepts_evolved`; connector and skill discovery remain open, while child agents stay disabled.

1. Design complete multi-factor hypotheses and their factor dependencies first. Acquire and
   verify local data through the Connector; check numerical correctness and representative
   planned strategies through the two environments. Defer finalization engineering until needed
   before test access, rather than blocking the first batch.
2. Archive explicit candidate/ablation/benchmark roles. Batch
   factor diagnostics and full strategy evaluation through the two environments. Form a
   working pool using measured evidence, improvement potential and behavioral diversity.
3. Refine each shortlisted strategy's factors/rules with matched comparisons, and include
   new hypotheses in subsequent rounds. Preserve rejected candidates and exact versions.
4. Review readiness before freezing one final bundle for both report sections. Interpret the
   frozen test honestly and justify completion or further exploratory research.

The [joint-exploration method](../../agentevolver/skill/finance/factor_strategy_research_skill/references/research-workflow.md#joint-exploration)
keeps factor and policy definitions open. Formal candidates require at least two distinct,
actually used factors. Single-factor controls and other ablations remain diagnostics, outside
candidate counts, the working pool and final selection. A marginal factor score does not gate every
exploratory strategy; evidence for the actual individual/conditional/group claim is required before final eligibility.
The skill owns the research and record layout. The prompt remains an orchestration layer.

The config mounts job only, alongside the dynamically authored research environments.
The researcher reads JSON directly; no browser or frontend-testing skill is mounted.
The report's HTML shell and local JS render the same JSON using the canonical visual theme,
and deploy_tool publishes a useful integrated snapshot on the 9876 gateway. The assembly
requires one report release, not a repeated presentation/redeployment loop.

The framework audit requires one connector and two distinctly named environments with
registration, evaluated keep and later real native consumer receipts. Registering two
versions of one environment is insufficient. The prompt/skill additionally requires different
factor and strategy responsibilities and numeric correctness. Runtime receipt auditing does
not independently judge financial validity or prove that the two implementations differ.

## Default study and test integrity

The v13 example uses TSLA daily bars over 2016-09-12–2026-09-11.
**Train covers 2016-09-12–2023-12-31; test covers 2024-01-01–2026-09-11.**
All discovery, factor and combination fitting, policy optimization and selection occur inside
train. The agent designs chronological rolling fit/score windows, support criteria, horizons,
gaps and statistical methods from training evidence. Actual label availability is checked
against every fit cutoff. There is no fixed annual fold count, universal IC/correlation
threshold or mandatory confidence level. User dates, costs and execution constraints remain
binding. See [study.json](../../examples/tasks/factor_strategy_mining/signal_foundry/study.json).

The historical test has already been examined in earlier project experiments, including the
S005-v003 diagnostic for e2dac449. The study preserves that disclosure. Reserving test from
this run's optimization does not make it project-wide unseen; its later evaluation is a
historical diagnostic, not new independent confirmation. Dates are not silently moved.

Research focuses on complete mechanisms: information and factor roles, conditional/group
relationships, combination fitting, entry, holding/exit, sizing and risk. Threshold/weight
search calibrates or tests a hypothesis; it is not a replacement for investigating a shared
information or policy failure. The agent chooses pool and batch sizes by evidence, behavioral
diversity and expected information/cost, without fixed candidate counts or structural quotas.
Simple fixed rules and train-fitted combinations are both supported.

Before each comparison, record the claim and evidence standard with reasons. Separate
marginal factor diagnostics, conditional/group evidence and exact-consumer value. Context
inputs do not universally require standalone directional-IC passes. Preserve failed claims;
new roles or revised criteria are new research, never retrospective passes. Freeze the final
standard, selected candidate and all fitted/refit policies before the one final operation.

The [integrity helpers](../../agentevolver/skill/finance/factor_strategy_research_skill/references/experiment-integrity.md)
are copied, pinned and invoked by the generated actions. They check dynamic training windows,
actual fitted rows, parent/control configuration differences and no-intervention decision
parity. Worker-owned completion receipts distinguish planned, dispatched, computed and observed
work; reconciliation recovers finished results without a round summary. One shared final
ledger reserves the bundle before retrieval and binds the downloaded snapshot before scoring.
These checks do not implement a backtester or independently judge financial merit.

The [completion review](../../agentevolver/skill/finance/factor_strategy_research_skill/references/research-workflow.md#completion-decision)
connects counterevidence, recent improvements and the expected value of further research.
Required failed/inconclusive evidence prevents readiness. No universal return target,
non-improvement patience or exhaustive search is imposed. Actual runtime limits mean
interruption, not successful completion.

Keep research completeness, strategy support and delivery separate. Test may support, refute
or leave the claim inconclusive. A negative conclusion can close research when diverse
attempts and ineffective revisions justify the low expected value of further work. Remaining
possible ideas do not force continuation. If no candidate qualifies, preserve the unexposed
test and explain why it was not run. Subsequent train work after exposure is exploratory; new confirmation
requires genuinely unused data and a prospectively fixed protocol. Record resource/access
interruptions and unfinished work without calling the research complete. Experiments do not
inherit previous run state; repeated historical studies are not independent new observations.

The runtime config declares a **protocol-only** access boundary. The agent can author code and acquire
data, so its self-written environment/ledger cannot enforce independence against itself.
Defer final-test retrieval/exposure, record access, and label the result as audited
self-evaluation. Historical returns may also be known to the model. For an enforced
private holdout, use a separately controlled evaluator and restrict the researcher's
data/write authority; this demo does not claim to supply that boundary.

## Data prerequisites

Verify data credentials and entitlements before running a long experiment. Provider discovery
starts with a small **training-only** request. Never print API keys or buy access automatically.
The [source reference](../../agentevolver/skill/finance/factor_strategy_research_skill/references/data-and-environments.md)
links provider contracts and public-data alternatives. Signal Foundry permits documented
public adjusted-price research, while strict as-traded/consolidated/action qualification is
reported separately as unmet when unverified. User dates and costs remain fixed; research criteria are prospectively versioned.
The first milestone is a native Connector download saved locally, with a checked hash,
nonempty OHLCV and exchange-session coverage. The skill includes a local snapshot checker
and a source-bound report adapter with executable line/bar charts using the visual theme.
If no source satisfies even the authorized research policy, publish a precise data blocker.
Synthetic data is only for engine fixtures. No broker orders or live trading are included.

## Work records and reports

The skill's [operator table and compiler](../../agentevolver/skill/finance/factor_strategy_research_skill/references/factor-expressions.md)
turn factor expressions into portable `compute_factors(DataFrame) -> DataFrame` modules with
a pinned operator runtime. Each factor version is an output column on the unchanged timestamp
index. The environment consumes that code for actual evaluation; the compiler does not run
backtests or admit factors. New operators can be added and verified when research needs them.

The agent expands the brief into the session's plan/plan.md and updates plan/index.md
with progress, current round, test state and exact JSON lookup paths. The skill suggests supporting records
under that directory without imposing a fixed framework schema. The domain layout uses
workspace/research/catalog.json, versioned factors/strategies, worker-owned trial receipts and immutable evaluations, and
rounds/R001/{batch,results,pool}.json. Each round has reports/v001 with analysis.json, the
HTML shell and local assets. Definitions, evaluation IDs, parent versions, comparisons and
selection decisions remain queryable. A report correction gets v002 without rerunning
unchanged backtests. Large results and data stay in the workspace. One continuous report contains all factor/strategy definitions and measured
effects, split comparisons, failed trials, chart interactions, uncertainty and downloadable
evidence. Both stages remain visible in normal page flow; navigation scrolls to anchors without
route or stage-tab switching. The HTML shell loads JSON; the researcher analyzes the JSON directly without browser work.
Report schema 3 also preserves each strategy's full archived spec: name, description, design,
parameters, exact bindings, version lineage and modification reasons, alongside route reviews
and measured comparisons. The skill's strategy_spec.py checks definition structure, pinned
implementation hashes and declared parent/control changes before evaluation; actual decision
parity is exercised through the generated comparison operation. The report derives metadata from a hash-bound
strategy_spec reference and rejects conflicting manually copied fields; schemas 1/2 remain
readable for existing artifacts. Environments export numerical admission and readiness evidence; the Agent applies
the skill's quality review to decide whether research is ready or needs further investigation.

The [metric contract](../../agentevolver/skill/finance/factor_strategy_research_skill/references/metrics-and-evaluation.md)
defines factor IC/RankIC, sample support, admission, portfolio accounting, performance/risk/cost
metrics, null states and numerical examples. The
[report specification](../../agentevolver/skill/finance/factor_strategy_research_skill/references/reports.md)
defines every chart's question, axes, series, scope and interpretation. Engines export versioned
results for the page and compact machine-readable analysis; the researcher records each
diagnosis, baseline/candidate metric change and next hypothesis under the shared plan directory.

## Relationship to the numerical benchmark

The former built-in factor environment and its dependent two-agent launcher, configuration
and specialized workers have been removed. Signal Foundry is the research demo entry point.
The independent [factor benchmark](../../agentevolver/benchmark/default/factor_mining/README.md)
retains expression evaluation, numerical checks, validation accounting and frozen final-test
grading under `agentevolver/benchmark/default/factor_mining/`. These are benchmark utilities,
not preloaded research environments or proof that a new capability passes this stock study.
Inspect lists runtime components only; read repository references through workspace tools
and inspect a newly created component only after successful registration.
