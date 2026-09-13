# Research workflow

Plan and run joint factor/strategy research, then decide whether further work is worthwhile.
The Agent chooses hypotheses, allocation and completion; the study supplies domain constraints.
[Metrics](metrics-and-evaluation.md), [data/interfaces](data-and-environments.md),
[expressions](factor-expressions.md) and [reports](reports.md) own their detailed contracts.

Sections: [records](#planning-and-records), [chronology](#protocol-and-chronology),
[joint exploration](#joint-exploration), [readiness](#readiness-review),
[final evaluation](#final-evaluation), [completion](#completion-decision).

## Planning and records

Use the shared plan module's `index.md` and `plan.md`. Design supporting files as useful;
only those two are framework defaults. Each experiment starts from supplied inputs and
built-in capabilities, without importing previous experiments' plans, generated components,
data, candidates or results. Preserve this experiment's history across turns and retries.

Keep the index brief: task/study paths, current round and pool, last verified result, next
operation, resources, test-exposure state and exact lookup paths. Update at meaningful result,
decision or blocker boundaries. The detailed plan links the protocol, data receipts/checks,
implementation status, trial history, candidate reviews, final bundle and delivery/capability
evidence. Large arrays and tables stay in files read on demand.

### Directory and version conventions

Choose a layout in plan.md and record its actual absolute roots in index.md. Recommended:

```text
plan/
  index.md                         # status and exact lookup paths
  plan.md                          # implementation and research allocation
  research/protocol.md             # prospective claims, criteria and chronology
  research/rounds/R001.md           # diagnosis and next hypotheses
  research/reviews/                # readiness, completion and capability evidence
workspace/research/
  catalog.json                     # ID/version -> definitions, results and reports
  trials.jsonl                     # append-only attempts and validation exposure
  data/<snapshot-id>/              # source receipt, data and checks
  factors/F001/v001/               # spec, generated code and compile receipt
  strategies/S001/v001/            # spec.json, policy code and bindings
  evaluations/<evaluation-id>/     # request, status, metrics, series and hashes
  rounds/R001/
    batch.json                     # joint candidates and dependencies
    results.json                   # candidate -> factor/strategy evaluation IDs
    pool.json                      # membership, reasons and next experiments
    reports/v001/                  # HTML shell, analysis.json, CSVs and assets
  final/<submission-id>/           # freeze, exposure marker, results and report
```

Use stable IDs and exact versions, independent of display names. A materially new hypothesis
gets a new ID; a revision retains lineage. An evaluation binds candidate versions, snapshot,
folds, fitted policy, engine, metric contract and costs. Changing those creates a new
assessment, not necessarily a new candidate definition. Never bind to `latest`.

Record trial starts before execution and terminal status/result paths afterward, including
errors and rejections. Reuse successful calculations with identical bindings; retries remain
visible but do not count as discoveries or reset exposure. Retry only failed dependencies.

Archived specs, completed results, pool snapshots and reports are immutable. Write new
artifacts atomically, then update the catalog/index; preserve round manifests and trial history.
A report correction needs a new report version, not a repeat of unchanged numerical trials.
Keep mutable drafting separate from published versions. An archived unimplemented proposal
also needs a new version when implementation is added.

Lookup: index → catalog/current round → compact JSON summary → selected evaluation/spec.
Read returned paths and selected fields rather than guessing filenames or dumping all curves.
HTML/JS visualize these same results for people; the Agent analyzes JSON without a browser.
Public artifact links must not expose credentials or internal filesystem paths.

### Strategy definition archive

Archive a complete `spec.json` before evaluating a strategy version. Schema 1 describes the
strategy without restricting its family, algorithm or parameter structure; extensions are
allowed. Names/descriptions are required, not inferred from IDs or filenames.

| Field | Contract |
| --- | --- |
| `schema` | Integer 1; separate from the report format version. |
| `strategy_id`, `version`, `id` | Stable ID, exact version and `strategy_id@version`. |
| `name`, `description` | Display name and standalone explanation of what it does, when and why. |
| `family`, `hypothesis`, `falsification` | Open mechanism label, testable hypothesis and contradicting evidence. |
| `created_round`, `parent_ids` | Origin round and exact parent versions; no parents for an initial proposal. |
| `change` | `kind`, `summary`, `reason`, `evidence_ids`; initial proposals use kind=initial, revisions cite parents and motivating evidence. Other kind labels remain open. |
| `factor_bindings` | `{factor_id, role, purpose}` entries using exact factor versions; empty only for explicitly marked baselines. |
| `design` | `objective`, `mechanism`, `combination`, `fit_policy`, `pseudocode`; nonempty `assumptions` and `failure_modes` arrays; `rules` for entry, exit, sizing, rebalance, neutral, risk and execution. |
| `parameters` | Open JSON object, possibly empty; explain parameter meaning in the design. |
| `implementation` | Null for a proposal; otherwise version-relative `path`, `entrypoint`, `sha256` and optional `dependencies` with relative paths/hashes. |
| `baseline` | Optional boolean, default false; controls remain described but are not discoveries. |

Describe signal timing, factor interactions, past-only fitting and signal-to-position rules.
State explicit absence where a rule is unused. Long notes may supplement the structured
design. A new independent hypothesis may lack prior numerical evidence; never fabricate it.

```bash
python {skill_dir}/scripts/strategy_spec.py /absolute/strategies/S001/v001/spec.json
python {skill_dir}/scripts/strategy_spec.py /absolute/strategies/S001/v001/spec.json --require-implementation
```

The checker validates structure and implementation/dependency file hashes without executing
code. Its `spec_sha256` hashes UTF-8 JSON with sorted keys, compact separators, unescaped
Unicode and no NaN. The engine still checks executable behavior, causality and referenced
factor/parent versions; pin package/runtime versions in the engine identity.

Embed the validated definition unchanged as `strategy_spec`, with `spec_sha256`, in each
strategy result. Keep fitted state, numerical scores, qualification and pool decisions in
evaluation/round records, not overwritten into the definition. Catalog entries link exact
IDs, name/description, spec path/hash, round/parents, evaluations and reports. Report schema 3
preserves the archived definition from the hash-bound source in analysis.json.

## Protocol and chronology

Before scoring, fix a versioned, hashed contract for source/adjustment basis, calendar/dates,
fit/score boundaries, labels, execution/costs, metric definitions, factor-role qualification,
comparison policy and evidence standards. Bind both engines and results to it. Honor study
constraints; additional roles or criteria must be prospective new versions, not changes that
rescue a failed claim. There is no universal return target or automatic search-count gate.

Use inclusive exchange-local session boundaries, translating provider range semantics.
Verify required completed sessions through the fixed cutoff; missing bars are acquisition
gaps, not permission to shorten the study. Hash accepted train/validation snapshots. For
deferred test acquisition, fix source identity/revision policy now and bind the actual file
hash after retrieval; never fabricate a hash or silently replace data.

Fit on strictly earlier observations. Purge labels crossing fold boundaries and apply the
study's session gap for the actual label/execution horizon. Record fit/score timestamps after
exclusions and warm-up. Past bars can warm features without being scored twice. Fit direction,
horizon, cutpoints, scaling, imputation, feature selection and policy weights only within the
allowed training prefix; never preprocess the full dataset or shuffle time-series samples.
Predeclared expanding refits may use earlier validation years, with that policy disclosed.
Validation is reused tuning data: retain all trials/looks and account for selection effects.

## Joint exploration

### Establish the executable research path

First download through the native Connector and verify a nonempty local OHLCV snapshot.
Implement both numerical Environments under the frozen contract and check deterministic
fixtures through their actual interfaces. Reuse local data for research; fixtures demonstrate
engineering behavior, not market performance.

Compile factor expressions and implement cash, matched buy-and-hold and a single-factor
policy baseline. Complete one real factor/strategy evaluation with actual metrics/series and
pass the [report adapter check](reports.md#report-adapter) before scaling search. Use the
bundled renderer; custom UI engineering is not a prerequisite for numerical exploration.

### Batch screening and candidate pools

The research unit is a **strategy hypothesis + its required factor versions/roles + executable
policy**. Design them together; compute factor diagnostics, then policy and contribution
results in the same research round. A global marginal-IC leaderboard is not a prerequisite:
apply [role-specific qualification](metrics-and-evaluation.md#roles-and-qualification-scope).
Exploratory consumer tests can establish that evidence, but do not imply eligibility.

For Signal Foundry, roughly ten initial strategies, about 100 cumulative factor definitions
and 20–30 distinct strategy hypotheses guide exploration. Adapt the schedule and size to
findings; these are neither minimum passing counts nor ceilings. Different strategies can
use different factor sets or share exact versions. Count economic mechanisms, evaluated
versions, parameter variants, baselines and execution failures separately. Explain scope
shortfalls without creating filler trials.

| Step | Work and decision |
| --- | --- |
| Propose | Choose falsifiable mechanisms, factors/roles and policy rules. Set benefit claims, baselines, qualification and guardrails before scoring. |
| Evaluate | Batch factor diagnostics and complete gross/net strategies on matched folds. Save all results/errors. Missing dependencies block only their consumers. |
| Select | Compare benefit, risk, costs, support, robustness, complexity, behavioral diversity and specific improvement potential. Pool membership means worth investigating, not qualified for final test. No survivor quota. |
| Refine | Review each shortlisted route; change factors, policy or both with an attributable comparison, or park/reject with evidence. Routes need not receive equal resources. |
| Replenish/review | Consider new mechanisms alongside revisions. Decide from expected information and cost whether another batch, final evaluation or completion is worthwhile. |

Use inexpensive common coverage, fold and net-performance diagnostics first. Concentrate
costly uncertainty checks, ablations and parameter neighborhoods on plausible candidates;
complete required evidence before final eligibility. Missing diagnostics remain pending.
Allow distinct return, risk-reduction or efficiency claims with prospective utilities and
tradeoff guardrails; never relabel an unsuccessful claim after seeing its results.

Examples for exploration include persistence/acceleration, recovery, failed breakout,
compression/expansion, gap/intraday behavior and risk or participation interactions. Invent
other justified mechanisms; daily OHLCV does not establish order-book or institutional activity.

### Refine factors and their consumers

Trace factor → target → order → return before choosing a change. Revise a factor when its
information, normalization, availability or conditional use is weak; revise policy when the
mapping, holding, sizing or costs lose useful information. Joint changes and cross-route
factor transfers are welcome when their incremental contribution can be tested.

Compare exact parent/candidate versions on matched dates, folds, costs and fitting policies.
Hold the consumer fixed for compatible factor comparisons and factors fixed for policy
comparisons; use small crossed comparisons when needed for interactions. Preserve gains,
regressions and rejected/incompatible revisions. A shared-factor revision cannot silently
change other strategies' historical bindings.

Keep factor discovery open throughout refinement. For promising routes investigate both
factor and policy limitations, rather than only permuting one leader's thresholds. Choose
changes with a falsifiable benefit; do not force a pointless revision to satisfy a counter.
Record why a proposed change was tested, deferred or rejected, including consumer evidence
when the change is compatible. The [diagnostic table](metrics-and-evaluation.md#evaluation-drives-the-next-experiment)
helps choose the next investigation.

### Diversity and efficient allocation

Assess mechanism diversity alongside measured behavior: aligned factor correlations,
strategy net-return/exposure correlations, active/entry overlap and fold/regime losses.
Use the metric contract's definitions; undefined correlation is not evidence of independence.
Renamed, affine or parameter-only copies do not establish a new hypothesis. Low correlation
alone cannot justify noisy formulas; a better IC alone cannot prove a better strategy.

Allocate batches between unexplored mechanisms, promising revisions and robustness checks.
Read compact summaries, then only the detailed JSON/CSV needed for decisions. Reuse completed
calculations with identical bindings and successful engineering checks until relevant changes
invalidate them. Avoid one conversation or report dump per factor and repeated low-information
tuning. Record time/cost alongside progress so presentation work cannot displace research.

Render local reports at meaningful research reviews and publish useful milestones/final
results, not every candidate. Check files, hashes, links and HTTP delivery directly; no browser
or screenshot review is needed. A changed training interval can check capability reuse;
additional stocks and a stock-search application are outside this single-stock brief.

## Readiness review

Before final test, review the exact candidate against the prospective evidence standard.
Use saved result IDs and include counterevidence; unknown is not passed.

| Dimension | Review |
| --- | --- |
| Mechanism/value | Falsifiable claim, qualified factor roles, baseline/ablation evidence and explicit tradeoffs. |
| Exploration | Distinct evaluated alternatives, each shortlist disposition, factor/policy revision evidence or reasons to defer, actual coverage versus guidance. |
| Robustness | Fold/regime consistency, parameter neighborhoods, concentration and uncertainty appropriate to the claim. |
| Feasibility | Cash/order reconciliation, costs/stress, turnover/exposure and sufficient labels/trades. |
| Selection effects | Complete trials/validation looks; claims calibrated to search and uncertainty. |
| Next work | Most useful remaining experiments and their expected information/cost; not an exhaustive inventory. |
| Delivery | Reproducible results, source-bound JSON/report and truthful data/capability status. |

Eligibility and readiness are separate from stopping. A promising candidate can be frozen
when evidence justifies it without exhausting other ideas. Apply the predeclared selection
policy and tie break; never select by a newly invented attractive metric. If evidence is
insufficient, choose a useful next experiment or an honest negative/inconclusive ending.

## Final evaluation

Freeze factors, fitting policy, strategy, engines, costs, comparison standard and data
identities together. Predeclare any pre-test refit and purge overlapping labels. Write the
exposure marker before test retrieval and bind the snapshot before scoring. Evaluate the
frozen bundle once for both factor and strategy report sections, with only predeclared
benchmarks/scenarios. Test diagnostics cannot select factors or replacements.

Interpret support, degradation, uncertainty and tradeoffs against the frozen claim. Interrupted
calculations may replay the same bundle with exposure preserved; post-reveal corrections keep
the original result and are diagnostic, not new confirmation. If no candidate is eligible,
leave test unexposed and record why final evaluation was not performed. Never submit a weak
candidate solely to fill the final panel or present missing test evidence as support.

A failed/inconclusive test neither forces further work nor automatically closes research.
Use the completion judgment. Useful later train/validation revisions remain exploratory;
new confirmation requires genuinely unused observations under a prospective protocol, not
repeated selection on the exposed period or silently changed task dates/symbols.

### Holdout trust boundary

Verify the boundary declared by `research.holdout_control` when supplied. The solo demo uses
`protocol_only`: deferred downloads, hashes and ledgers reduce accidental leakage, but an
Agent-authored editable environment cannot prevent its author from refetching data. Describe
this as audited self-evaluation, not enforced isolation. Historical stock selection and prior
knowledge also limit claims; restarting a historical study does not create new observations.

Enforced isolation requires an evaluator outside the researcher's write/data authority. The
`factor_mining` Benchmark/bridge illustrates this but is not mounted automatically. Establish
and test that boundary if required, or report it unavailable.

## Completion decision

The Agent decides after meaningful research reviews, using the goal, evidence, recent
improvement history and expected value of the next experiment. Save a concise review linked
from index.md: decision/reason, exact result IDs, progress and counterevidence, unresolved
limitations, candidate support, test state, delivered artifacts and any unmet requirements.

| Decision | When justified |
| --- | --- |
| Continue | A concrete feasible experiment or repair is likely to resolve an important uncertainty or materially improve the result at reasonable cost. State the next hypothesis. |
| Complete with a supported strategy | The stated optimization/research objective is supported by required evidence, including the frozen test, and requested deliverables are ready. Other possible ideas need not be exhausted. |
| Complete with a negative/inconclusive conclusion | Credible exploration and revisions show repeated ineffective improvement, weak support or low expected value from further work. Explain the scope tested and why stopping is reasonable; never claim no possible useful strategy exists. No eligible candidate means final test may remain unperformed. |
| Interrupted/blocked | A user stop, actual runtime/resource limit or external prerequisite prevents planned useful work. Preserve partial results, the actual cause and a restartable next step. |

For stagnation, compare recent meaningful attempts with their parents and consider whether
a different mechanism offers a worthwhile alternative. The Agent chooses the review horizon;
there is no mandatory patience counter, candidate minimum, return threshold or requirement to
prove exhaustive search. One failed tweak alone is weak evidence, but unused budget or a
merely conceivable idea is not an obligation to continue. Explain deviations from exploration
guidance and remaining limitations; do not generate filler to meet counts.

Stopping research does not relax numerical qualification, conceal failed tests or turn a
broken pipeline into a negative market result. Finish the report and preserve available
results before a planned completion. Keep research completeness, strategy support, strict
source qualification, delivery and capability evolution separate. Use done_tool's required
outcome: a justified negative conclusion can be completed; unfinished work is resource_limited
or blocked only with its actual cause. A report release or context compaction is not a stop
condition, and tests must not be reused until a winner appears.
