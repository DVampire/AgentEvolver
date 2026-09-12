# Factor and factor strategy mining

Signal Foundry is a **single-agent, two-stage research demo**. The agent develops its
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
do not mandate spending the budget. The study separately bounds research trials and validation.
Runs register on the usual gateway at port 9876; deploy_tool publishes the report links.

## Assembly and responsibilities

| Layer | Location / role |
| --- | --- |
| Agent | `agentevolver/agent/actor/factor_strategy_mining_agent.py`: thin MetaAgent specialization with child dispatch disabled |
| Prompt | `agentevolver/prompt/default/factor_strategy_mining_agent.html`: two-stage ownership and integrity rules; includes shared evolution rules |
| Skill | `agentevolver/skill/finance/factor_strategy_research_skill/`: planning, data/interfaces, research protocol, numerical evaluation contract and single-page report methods |
| Config | `configs/factor_strategy_mining_demo.py`: exactly one actor; Bash, job, browser, inspection, adoption, deployment and completion |
| Task | `signal_foundry/task.html`: English product outline and acceptance requirements; no inline style or scripts |
| Study | `signal_foundry/study.json`: stock, dates, costs, research budgets and numerical objectives |
| Runtime policy | `task_manifest_defaults` in the demo config: deployment, self-review, evolution evidence and declared holdout control |

No market connector or factor/strategy environment is preloaded in this demo. The agent
lists currently loaded capabilities with `inspect_tool(capability_type=...)`, inspects
exact returned names, establishes concrete limitations and develops or improves
an appropriate Connector and **two distinct Environment components**. Environments can
share verified numerical utilities. The router makes registered environments callable via
`accepts_evolved`; connector and skill discovery remain open, while child agents stay disabled.

1. **Factor research:** authorize source access, acquire real OHLCV/corporate actions,
   develop the factor environment, evaluate causal hypotheses, admit factor versions and
   publish the Factor Observatory section of the continuous report.
2. **Strategy research:** develop the strategy environment, combine admitted factors,
   backtest training and validation, inspect costs/ablations, return to factor research
   when evidence warrants it, then extend the same page with the Strategy Atelier.
3. Freeze one final bundle before revealing test for **both** report sections. Report the frozen
   test outcome, uncertainty, failed gates and reproducibility artifacts.

The framework audit requires one connector and two distinctly named environments with
registration, evaluated keep and later real native consumer receipts. Registering two
versions of one environment is insufficient. The prompt/skill additionally requires different
factor and strategy responsibilities and numeric correctness. Runtime receipt auditing does
not independently judge financial validity or prove that the two implementations differ.

## Default study and test integrity

The provided example uses NVDA daily bars, train 2016–2020, expanding annual validation
2021–2023 and final test 2024-01-01 through 2026-09-11, with a minimum 21-session boundary gap.
The September cutoff includes only completed regular trading sessions; it does not request
future September dates or unfinished daily bars. The cutoff stays fixed once the study starts.
See [study.json](../../examples/tasks/factor_strategy_mining/signal_foundry/study.json) for the complete research specification. Default final-test objectives jointly require net CAGR ≥12%,
net Sharpe ≥1, drawdown ≤25%, at least 20 closed round trips and 400 scored sessions,
Sharpe no worse than holding NVDA and positive return under doubled trading costs.
These are **illustrative demo targets**, not an expected or promised return. Change the
input specification before a new study starts, never to rescue a failing observed result.

Ordinary iterations use train/validation. The same held-out test cannot guide repeated
tuning: failed/inconclusive test or exhausted research budget yields an unsuccessful
outcome with usable reports. A new directory does not make previously seen data unseen.

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
links official Alpaca and Alpha Vantage contracts. Consolidated volume, requested coverage,
splits/dividends and exchange sessions must be verified, not inferred from an endpoint name.
If no authorized source satisfies the study, the agent should publish a precise data blocker.
Synthetic data is only for engine fixtures. No broker orders or live trading are included.

## Work records and reports

The agent expands the brief into the session's plan/plan.md and updates plan/index.md
with progress, current stage, test state and paths. The skill suggests supporting records
under that directory without imposing a fixed framework schema. Large results and data stay
in the workspace. One continuous report contains all factor/strategy definitions and measured
effects, split comparisons, failed trials, chart interactions, uncertainty and downloadable
evidence. Both stages remain visible in normal page flow; navigation scrolls to anchors without
route or stage-tab switching. Two research-bearing releases update that same product over time.

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
