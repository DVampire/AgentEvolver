# Research workflow

Plan and run joint factor/strategy research, then decide whether further work is worthwhile.
The Agent chooses hypotheses, allocation and completion; the study supplies domain constraints.
[Metrics](metrics-and-evaluation.md), [data/interfaces](data-and-environments.md),
[expressions](factor-expressions.md) and [reports](reports.md) own their detailed contracts.

Sections: [records](#planning-and-records), [chronology](#protocol-and-chronology),
[joint exploration](#joint-exploration), [allocation](#staged-evaluation-and-route-scheduling), [readiness](#readiness-review),
[final evaluation](#final-evaluation), [completion](#completion-decision).

## Planning and records

Use the shared plan module's `index.md` and `plan.md`. Design supporting files as useful;
only those two are framework defaults. Each experiment starts from supplied inputs and
built-in capabilities, without importing previous experiments' plans, generated components,
data, candidates or results. Preserve this experiment's history across turns and retries.

Keep the index brief: task/study paths, current pool, latest evaluated research revision,
next ready experiment, resources, test-exposure state and exact lookup paths. Separate
engineering, candidate screening, actual refinement and delivery progress; show pending work.
Update at result, decision or blocker boundaries. The detailed plan links the protocol, data receipts/checks,
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
also needs a new version when implementation is added. Such an implementation-only version
does not count as a research refinement. A refinement records changed factor/policy behavior
and its measured parent comparison, including regressions.

The live index, catalog and resource counters are navigation/status records, not immutable
evidence. Reviews and reports bind completed results and definitions directly; if a catalog
is needed as evidence, first save and hash an immutable snapshot. Publish the report, then
update the live catalog. A later index or cost update does not invalidate numerical evidence.

Lookup: index → catalog/current round → compact JSON summary → selected evaluation/spec.
Read returned paths and selected fields rather than guessing filenames or dumping all curves.
HTML/JS visualize these same results for people; the Agent analyzes JSON without a browser.
Public artifact links must not expose credentials or internal filesystem paths.

### Strategy definition archive

Archive a complete `spec.json` before evaluating a strategy version. Schema 2 describes the
strategy without restricting its family, algorithm or parameter structure; extensions are
allowed. Names/descriptions are required, not inferred from IDs or filenames.

| Field | Contract |
| --- | --- |
| `schema` | Integer 2; separate from the report format version. Schema 1 remains readable for historical archives, without inferring candidate status. |
| `strategy_id`, `version`, `id` | Stable ID, exact version and `strategy_id@version`. |
| `name`, `description` | Display name and standalone explanation of what it does, when and why. |
| `family`, `hypothesis`, `falsification` | Open mechanism label, testable hypothesis and contradicting evidence. |
| `created_round`, `parent_ids` | Origin round and exact parent versions; no parents for an initial proposal. |
| `change` | `kind`, `summary`, `reason`, `evidence_ids`; initial proposals use kind=initial, revisions cite parents and motivating evidence. Other kind labels remain open. |
| `research_role`, `control_for` | `candidate`, `ablation` or `benchmark`. Ablations name exact full-candidate versions in nonempty `control_for`; others use `[]`. |
| `factor_bindings` | `{factor_id, role, purpose}` entries using exact versions. Candidates need at least two distinct identities with no fixed upper limit. Within one strategy bind only one version per identity; other strategies may reuse the same versions. Controls may have fewer. |
| `design` | `objective`, `mechanism`, `combination`, `fit_policy`, `pseudocode`; nonempty `assumptions` and `failure_modes` arrays; `rules` for entry, exit, sizing, rebalance, neutral, risk and execution. |
| `parameters` | Open JSON object, possibly empty; explain parameter meaning in the design. |
| `implementation` | Null for a proposal; otherwise version-relative `path`, `entrypoint`, `sha256` and optional `dependencies` with relative paths/hashes. |
| `baseline` | Legacy flag; omit in new definitions. If present it must agree with research_role (true for controls). |

Describe signal timing, factor interactions, past-only fitting and signal-to-position rules.
State explicit absence where a rule is unused. Long notes may supplement the structured
design. A new independent hypothesis may lack prior numerical evidence; never fabricate it.
Use `design.objective` to reference the prospective comparison standard and tradeoffs;
`mechanism`, `combination`, `fit_policy` and `rules` describe how this route differs from
its peers. These existing fields suffice; no extra mandatory planning files are needed.

```bash
python {skill_dir}/scripts/strategy_spec.py /absolute/strategies/S001/v001/spec.json
python {skill_dir}/scripts/strategy_spec.py /absolute/strategies/S001/v001/spec.json --require-implementation
```

The checker validates structure and implementation/dependency file hashes without executing
code. Validate one representative definition before generating a family. An implementation
path is relative to its version directory (e.g. `policy.py`); `implementation: null` means
an unimplemented proposal. A derived control may start at v001 but still names its full
candidate in `parent_ids`/`control_for` and uses a non-initial `change.kind`.
Its `spec_sha256` hashes UTF-8 JSON with sorted keys, compact separators, unescaped
Unicode and no NaN. The engine still checks executable behavior, causality and referenced
factor/parent versions; pin package/runtime versions in the engine identity.

Candidate counts and pool/final eligibility use research_role, not filenames or display names.
Single-factor policies, including promising ones, are diagnostic controls. Two bindings alone
do not prove meaningful use: review executed dependencies and contribution evidence. If a
revision leaves only one useful factor, keep it as a control and redesign the combination or
park the route. Do not add an inert factor merely to regain candidate status.

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
rescue a failed claim. Use the [objective contract](metrics-and-evaluation.md#prospective-objectives-and-comparisons)
to define benefit, utility, benchmarks and acceptable sacrifices. Different permitted claims
can coexist, but do not compare their utilities as one ranking or relabel a failed route.
Preserve the study's final-selection policy and test budget. There is no universal return
target or automatic search-count gate.

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

Separate label/fit isolation from the investable account calendar using the
[accounting scope contract](metrics-and-evaluation.md#portfolio-accounting-and-scope).
State which scope controls selection and what full-calendar evidence the study requires.
Check these conventions on a small fixture before broad scoring; implement secondary
candidate comparisons when a route merits qualification. Do not make every candidate's
deep calendar analysis a prerequisite for screening.

## Joint exploration

### Design the batch, then establish its numerical path

Before engineering a pilot, record the initial strategy batch in round/batch.json. For each
hypothesis define its mechanism and falsification, required factor expressions/roles, how
they interact, past-only fitting and entry/exit/sizing rules. A list of strategy names is not
a designed batch. Link shared exact factors rather than manufacturing a separate copy for
every strategy. Plan alternative mechanisms beyond the first example; do not let the pilot's
two factors become the whole search universe.

Vary both the information hypothesis and how information becomes a position. For example,
compare continuous score blending, interactions or conditional weighting, event-memory/exit
rules, or a compact training-fitted policy when justified. These are options, not required
families. Different formulas inside the same threshold-AND-risk template cover only a narrow
policy search. Record the distinguishing combination, fitting and holding choices in each
design; sample materially different planned policies in the pilot so its engine does not
hard-code one mapping, number of factors or default holding horizon.

Acquire and verify local train/validation OHLCV through the native Connector before numerical
research. Build the two Environments' evaluation path for representative planned candidates:
factor computation, causal fitting, signals/orders/accounting and compact source-bound JSON.
Check relevant hand-computable fixtures, the [concurrent execution contract](data-and-environments.md#batch-execution-and-result-summaries)
and a small real multi-factor pilot, then execute the initial batch. Cache shared factors/folds;
retry failed dependencies without rerunning successes. Use concurrent evaluation for independent
work when resources and batch size justify it; record workers, elapsed time and any reason for
serial execution in the round summary. A batch API or async signature alone is not concurrency.

Build capabilities incrementally. Verify accounting and causality before trusting research
numbers; verify freeze/access/replay safeguards before using final test. Do not implement the
entire final-test/report pipeline as a prerequisite for the first broad batch. Track deferred
operations explicitly and report capability readiness only for exercised operations. Use the
bundled renderer at meaningful research reviews; early research reads numerical JSON directly.

### Batch screening and candidate pools

The research unit is a **multi-factor strategy hypothesis + its factor versions/roles + executable
policy**. Design them together; once each policy's factor values and past-only fitted inputs
are ready, run factor diagnostics and strategy backtests concurrently. Join the evidence for
contribution review and selection in the same round. A global marginal-IC leaderboard is not a prerequisite:
apply [role-specific qualification](metrics-and-evaluation.md#roles-and-qualification-scope).
Exploratory consumer tests can establish that evidence, but do not imply eligibility.

For Signal Foundry, roughly ten initial strategies, about 100 cumulative factor definitions
and 20–30 distinct strategy hypotheses guide exploration. Adapt the schedule and size to
findings; these are neither minimum passing counts nor ceilings. Different strategies can
use overlapping factor sets from one shared library. Factor count follows the mechanism:
four, five or more distinct inputs are welcome; two or three is not a ceiling or an optimum.
For example, one strategy may bind F001–F005 and another F001/F003/F006–F009, all at exact
versions, with different weights, interactions and holding rules. Shared inputs count once
in the factor inventory, while consumer tests remain strategy-specific. All fitting is past-only.
Count proposed/evaluated mechanisms, versions, parameter variants, controls and errors separately. Explain scope
shortfalls without creating filler trials.

After the initial screen, default to **3–5 active strategy routes**, following the study's
working-pool target when supplied. Select by measured promise, distinct behavior and a
specific improvement hypothesis. Prioritize joint factor/policy refinement of these routes;
versions and controls do not occupy new route slots. Keep fewer if evidence does not justify
three, rather than padding with weak or duplicate candidates. Additional ideas can receive
cheap screening and replace parked/weaker routes; record the replacement reason instead of
continually expanding deep evaluation across every historical candidate.

### Staged evaluation and route scheduling

Choose evaluation depth explicitly in the request/receipt. This changes work allocation,
not the study's thresholds, chronology, costs or final evidence requirements.

| Depth | Evidence and next decision |
| --- | --- |
| Screen | Valid data/causal fitting/accounting; factor coverage and role point metrics; strategy net/stressed performance, folds, activity, exposure and costs. Retain, reject or identify a concrete repair. Expensive uncertainty/contribution checks may remain pending. |
| Refine | Test a changed factor expression, combination, fitting, entry/exit or sizing rule against its parent on matched scopes. Add the smallest diagnostic needed to choose that change; preserve regressions and pending qualification. |
| Qualify | Complete required factor roles, exact-consumer contributions, redundancy, uncertainty, stability and search review for a candidate that merits final submission. Freeze and access test only after readiness passes. |

Maintain a ready-work queue in the existing pool/round record. Each retained route names its
next changed candidate and comparison, or one unresolved question whose answer selects the
edit. Record the expected decision and cost before scheduling that diagnostic. A weak route
can be parked with its current evidence; a full ablation sweep is not its default disposition.
Choose additional controls only when they can change a research decision. Unknown evidence
stays pending; a missing qualification receipt does not prevent exploratory research.

Advance each route when its own inputs and required diagnosis are ready. Do not wait for
all factors, every route's controls or a report release before testing a useful revision.
Round IDs group records, not synchronization barriers. Independent diagnostic, refinement
and new-mechanism trials can run together through the bounded environment workers. Reuse
successful materializations and evaluations; shared factor changes never mutate old consumers.

If checks keep accumulating while candidate behavior stays unchanged, reassess the queue:
execute the best supported revision/new hypothesis, resolve a concrete blocking defect, or
park the route. Verification of unchanged results is not evidence of ineffective optimization.
Judge progress by compared changes and explored mechanisms, not tool calls or version counts.

Keep promising factor versions discoverable in the same catalog when a consumer fails.
Record factor-role results, exact consumer contributions and whole-strategy eligibility
separately: a useful risk forecast is not universally useful, and a failed strategy does
not erase its inputs' measured evidence. A transfer needs a new hypothesis and consumer test.

Examples for exploration include persistence/acceleration, recovery, failed breakout,
compression/expansion, gap/intraday behavior and risk or participation interactions. Invent
other justified mechanisms; daily OHLCV does not establish order-book or institutional activity.

### Refine factors and their consumers

Trace factor → target → order → return before choosing a change. Separate weak information
from lost information in the combination, holding, sizing or execution. Check forecast
horizon versus actual holding time, train-selected orientation, inactive opportunity and
costs before adding filters. Positive IC with weak returns warrants a mapping experiment,
not automatically another factor. Joint changes and cross-route transfers remain open.
Check training-time entry reachability and fitted direction versus policy conditions before
scaling an event strategy. Diagnose low exposure, missed opportunities and multiplicative
filters before adding another gate. Low return from a defensive allocation may match its
claim; compare sizing/holding alternatives under the declared objective rather than assuming
more filters or larger positions improve the information signal.

In the existing route record, connect the main diagnosis to a discriminating comparison:
name plausible explanations, the changed component, the fixed parent/control, the measured
quantity and how either outcome changes keep/revise/park. For sparse participation, this
may separate entry filtering from holding or risk sizing; for a horizon mismatch, compare
the corresponding label evidence and holding behavior. Use a bounded crossed comparison
only if one change cannot distinguish the interaction. Repeatedly noting low exposure or
weak IC without a decision is not refinement. If a comparison is not worthwhile, explain
why from current evidence rather than imposing a minimum exposure or mandatory edit.

Compare exact parent/candidate versions on matched dates, folds, costs and fitting policies.
Hold the consumer fixed for compatible factor comparisons and factors fixed for policy
comparisons; use small crossed comparisons when needed for interactions. Preserve gains,
regressions and rejected/incompatible revisions. A shared-factor revision cannot silently
change other strategies' historical bindings. Publish a revised shared factor as a new
version, compare its effects on chosen consumers, and record each consumer's keep/replace
decision independently; improvement in one policy does not upgrade all of them.

Keep factor discovery open throughout refinement. For promising routes investigate both
factor and policy limitations, rather than only permuting one leader's thresholds. Choose
changes with a falsifiable benefit; do not force a pointless revision to satisfy a counter.
Record why a proposed change was tested, deferred or rejected, including consumer evidence
when the change is compatible. The [diagnostic table](metrics-and-evaluation.md#evaluation-drives-the-next-experiment)
helps choose the next investigation.
Weak standalone evidence with useful consumer contribution is a reason to investigate a
prospectively defined interaction or conditional use, not to repeatedly retest the same
failed definition. Preserve its original failed claim and apply the study's role rules to
the new hypothesis; a profitable aggregate does not retroactively qualify its inputs.

### Diversity and efficient allocation

Assess diversity at both design and behavior levels: information source/role, combination,
fitting and holding mechanism; then aligned returns/exposures, entry overlap and fold/regime
losses. Group close relatives and count formula changes, policy changes and new hypotheses
separately. Undefined or low correlation alone does not establish a useful new mechanism.
Different transforms of the same OHLCV inputs add mappings, not new information sources.
When failures are shared, test the shared information or policy limitation before adding
similar formulas. Broader sources or symbols require a study that permits them; a formula
count shortfall does not justify expanding the task.

Allocate work between unexplored mechanisms, promising revisions and resolving uncertainty.
If many routes fail for the same reason, investigate that shared limitation before extending
the same template. Read compact summaries, reuse identical calculations and valid engineering
checks, and spend new evaluations on decision-changing comparisons. Record time/cost with
progress; presentation cannot displace research. No fixed family quota or exhaustive search.

Render local reports at meaningful research reviews and publish useful milestones/final
results, not every candidate. Use a completed snapshot while ready research continues; report
polish and unrelated diagnostic completion are not prerequisites for the next trial.
Check files, hashes, links and HTTP delivery directly; no browser
or screenshot review is needed. A changed training interval can check capability reuse;
additional stocks and a stock-search application are outside this single-stock brief.

## Readiness review

Before final test, review the exact candidate against the prospective evidence standard.
Use saved result IDs and include counterevidence; unknown is not passed.
Explain the strongest alternative account of the apparent benefit, such as lower exposure,
one favorable period or selection on repeatedly reused validation. Link its existing
comparison, or identify the decision-changing check still needed. Being the only eligible
candidate is not itself evidence against these explanations. Keep formal gate verdicts
separate from the strength and scope of the research conclusion; do not invent new numeric
thresholds at this review or require all diagnostic intervals to exclude zero.

| Dimension | Review |
| --- | --- |
| Mechanism/value | Formal multi-factor candidate, actually used and qualified factor roles, baseline/ablation evidence, falsifiable claim and explicit tradeoffs. |
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
a different mechanism offers a worthwhile alternative. Initial proposals, implementation
versions and unchanged-policy diagnostics do not establish repeated ineffective improvement.
If no meaningful revisions ran, describe the actual constraint and the unexplored opportunity.
The Agent chooses the review horizon;
there is no mandatory patience counter, candidate minimum, return threshold or requirement to
prove exhaustive search. One failed tweak alone is weak evidence, but unused budget or a
merely conceivable idea is not an obligation to continue. Explain deviations from exploration
guidance and remaining limitations; do not generate filler to meet counts.

After deciding to close research, record that decision once and finish the bounded
[report delivery](reports.md#artifact-and-numerical-acceptance). Reopen research only when
new evidence undermines the conclusion or a useful new experiment is justified. Report
styling, live cost updates and a deployment prerequisite do not require another evaluation
round or repeated whole-project audits. Diagnose an unchanged publication blocker once;
repair its actual cause, or record the unavailable prerequisite and the usable preview.

Stopping does not relax numerical qualification, conceal failed tests or turn a broken
pipeline into a negative market result. Keep research completeness, strategy support, strict
source qualification, delivery and capability evolution separate. Use done_tool's required
outcome: a justified negative conclusion can be completed; unfinished delivery remains
blocked with its actual cause. Use resource_limited only for an actual resource limit.
Neither a report release nor context compaction determines whether research should stop.
