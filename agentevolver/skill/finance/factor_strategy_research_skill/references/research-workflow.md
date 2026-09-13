# Research workflow

Use this reference to plan the investigation, allocate experiments and judge readiness or
completion. Numerical definitions belong to [metrics and evaluation](metrics-and-evaluation.md);
source acquisition and engine interfaces belong to [data and environments](data-and-environments.md).

Sections: [planning and records](#planning-and-records),
[protocol and chronology](#protocol-and-chronology),
[joint exploration](#joint-exploration), [readiness review](#readiness-review),
[final evaluation](#final-evaluation), [completion decision](#completion-decision).

## Planning and records

Use the shared plan module's supplied `index.md` and `plan.md` paths. The task HTML describes
the research product; the study specifies market assumptions and domain constraints. Keep
their staged paths in the index so they survive compaction. Framework components, storage,
adoption and deployment belong to prompts/skills; runtime evidence requirements come from
the configured input manifest. The Agent designs the implementation in the detailed plan.

Each experiment starts from its supplied inputs and built-in capabilities, with new plans,
local data, implementations and trial/exposure records. Do not import earlier experiments'
plans, memories, generated components, candidates, results or exhausted budgets. Within
this experiment, preserve its complete history across turns, retries and revisions.

Keep the index concise: current status, last verified result, next experiment, remaining
resources, holdout state, active-route summary and links to authoritative records. Update
at meaningful implementation, evaluation, decision or blocker boundaries; reconcile with
this run's files before repeating probes. Large tables, arrays and full reviews stay in
files read on demand. Distinguish the last frozen attempt's outcome from current exploratory
work, and track eligibility, submission readiness and final support separately.

The detailed plan covers the following responsibilities. Choose additional filenames and
subdirectories under the plan directory as useful; only index.md and plan.md are defaults.

| Record responsibility | What to preserve |
| --- | --- |
| Data and implementation | First native download receipt, accepted file/hash, OHLCV/calendar checks, source semantics and strict qualification; connector and two environment interfaces, shared utilities, successful operations and missing implementation. |
| Research contracts | Frozen chronology, source identities, metric contract, role-specific qualification, comparison/evidence standards, resource allocation and actual holdout boundary. |
| Trials and research routes | Stable candidate/version, parent, factor binding/role, hypothesis, falsification, fold, engine and result IDs; every parameter trial and validation look, including errors/rejections, exposure and cost. |
| Decisions and readiness | Baseline/candidate values and gate failures, diagnosis, next bounded hypothesis, measured improvement/regression, keep/revise/park/reject rationale, every shortlist review and remaining investigations. |
| Final evaluation | Exact frozen submission, readiness evidence, pre-access exposure marker, retrieved snapshot identity, results and failed attempts; subsequent exploratory revisions remain separately versioned. |
| Product and capabilities | One-page report design, data exports, visual reviews and browser evidence; capability baselines, exact-version evaluations, adoption decisions and real consumer receipts. |
| Completion review | Evidence and counterevidence by quality dimension, remaining work, claims/support and the reasoned continue/complete/interrupted decision. |

Record trials and validation requests before execution; identical replays are not new
search trials and never reset exposure or usage. Pending hypotheses are not executed research.
Keep large snapshots, numerical results, source and website assets in the workspace, linked
by absolute paths and hashes from plan records. Public downloads use permitted relative
artifact URLs without internal paths or credentials. A missing check remains pending.

Track source access, engineering verification, real-data research and report delivery
separately. An adopted environment is not automatically research-ready. On a blocker record
the actual dependency, useful independent work and next action; "test failed" is not a plan.

## Protocol and chronology

Before performance search, write a content-hashed contract fixing data identity,
market/calendar/frequency, dates, fit/score boundaries, horizons, execution timing,
adjustments, costs, metric definitions, role-specific factor qualification, diversity and
route-review requirements, comparison policy, evidence standards and resource allocation.
Bind the metric-contract version to both engines and the report. Honor supplied constraints
and the explicit public-data research policy; do not weaken them after seeing results.
Unsupported changes of task scope require user input.

Candidate formulas, mechanisms and route names remain open to discovery. Freeze any new
role or additional selection criterion before its first scored use, preserving parent
criteria and failures. Define the claim, matched baseline, scope, decision method, sample
and uncertainty support, and falsification condition prospectively. Tolerances can encode
data validity, risk assumptions or statistical support; they are not a universal return
target. Signal Foundry has no default CAGR/Sharpe target, candidate/round ceiling or
non-improvement patience limit. Missing ceilings do not authorize inventing one.

Interpret date boundaries as inclusive exchange-local session dates, translating provider
range semantics explicitly. Exclude unfinished sessions when required; verify every required
completed session through the declared cutoff. Missing final bars are incomplete acquisition,
not permission to shorten the study. Never automatically extend its cutoff during a run.
Hash train/validation snapshots on acquisition. For deferred test data freeze provider,
symbol, interval, feed, session, adjustment and source-revision policy; bind the actual file
hash only after the permitted retrieval, never fabricate it or silently replace a snapshot.

Use chronological train, validation and test ranges with the study's expanding folds.
Fit each fold on strictly earlier observations; purge labels crossing the evaluation
boundary and apply the declared trading-session gap, covering the actual label/execution
horizon. Record actual fit/score timestamps after exclusions and warm-up. Past bars may
warm up features without being scored twice or used to fit later-data transformations.

Fit direction, primary horizon, quantile cutpoints, scaling, clipping, imputation, feature
selection, regime classifiers and strategy weights inside the permitted training fold.
Never preprocess the full dataset before splitting or shuffle time-series samples. A
predeclared expanding refit may use earlier validation years; disclose that policy and
retain all selection attempts. Validation is a repeatedly used tuning resource, not fresh
independent evidence after every look. Preserve all trials and search uncertainty, not only
the winning row. The supplied calendar/gap needs explicit label-aware purging beyond a
generic [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

## Joint exploration

### Establish the executable research path

Make a native Connector download to a verified local file the first executable milestone;
follow [data acquisition and recovery](data-and-environments.md). Then implement the frozen
numerical contract in the factor and strategy Environments, with deterministic fixtures and
real successful native operations. Reopen the accepted snapshot rather than downloading
prices for each candidate. Fixtures establish engineering behavior, not market performance.

Compile factors through the [expression interface](factor-expressions.md), evaluate real
definitions and publish measured factor evidence on the continuous report. Start strategy
work with cash, matched buy-and-hold and executable single-factor baselines, then evaluate
distinct policy hypotheses and add their results to that same page. Before scaling search,
complete one real factor evaluation and one strategy baseline, export actual metrics/series
and pass the [report adapter check](reports.md#report-adapter). This integration milestone
does not admit a failed factor, reveal test or replace fold admission and finalization checks.

### Maintain a portfolio of mechanisms

A research route links a falsifiable mechanism, exact factor versions, an executable
strategy hypothesis, expected failure conditions and a bounded next experiment. It is a
work record, not a child agent. Invent routes within lawful inputs and the execution model;
task examples are invitations, not a catalog. Draw proposals from unexplored mechanisms,
diagnosed weaknesses and transfers/combinations with testable incremental value.

Evaluate the study's minimum distinct factor and strategy mechanisms before concentrating
search. Baselines and parameter variants consume trials but do not establish coverage;
count a mechanism only after valid numerical evaluation. Report unmet coverage if an actual
limit prevents it. Keep promising representatives with different net-return, risk, robustness,
cost and behavioral tradeoffs. This frontier guides exploration; final selection still uses
the predeclared comparison policy rather than silently choosing highest Sharpe.

Review every shortlisted route, including those below the leader. Give each a diagnosis and
a bounded refinement or evidenced park/reject decision. Weak routes need neither equal
resources nor permanent retention. An initial admitted library does not close factor discovery.

### Refine factors and their consumers

Inspect the factor → target → order → return chain and choose a falsifiable change:

- Revise/invent a factor when its mechanism, normalization, availability, horizon or
  conditional usefulness is inadequate; version expressions and fitted policies.
- Revise the strategy when entry/exit, holding, sizing, combination or costs lose useful
  information. Different strategies can use different factors and signal-to-position rules.
- Revise both when a mechanism needs another consumer; explain the dependency and test
  compatible components separately when that comparison can resolve attribution.
- Transfer a factor to another route against the recipient's baseline. Shared factors are
  allowed; a global top-k library is not every strategy's required input.

Compare parent/candidate on matched research dates, folds, costs and fitting policy.
Where compatible, hold the consumer fixed for a factor revision and factors fixed for a
policy revision; a small crossed comparison can resolve interactions without a full
Cartesian search. Train fitted components only on allowed past data. Log gains and
regressions; joint changes contribute to both factor and strategy trial histories.

Pin each strategy's exact factor versions, roles, qualification scope, implementation and
fitted-state hashes. Never resolve historical bindings against `latest` or silently update
other consumers of a shared factor. Rejected factors stay rejected; a new role/hypothesis
needs a prospectively defined new version/trial and scoped evidence under the
[qualification contract](metrics-and-evaluation.md#roles-and-qualification-scope).

Joint refinement requires an executed factor revision motivated by evidence and its
downstream strategy comparison, plus a strategy-refinement comparison. They may belong to
different routes and may fail. If a factor revision is rejected/incompatible, preserve its
numerical rejection and explain why consumer testing is invalid. An initial library followed
only by strategy parameter permutations is unfinished joint exploration.

### Measure diversity and allocate work

| Level | Evidence |
| --- | --- |
| Hypotheses | Mechanism, inputs, role, expected response and failure regime. Affine renaming, lookback changes and resized copies remain variants of the parent. |
| Factors | Aligned correlations, sample support, training-defined conditional behavior and role-specific diagnostics; distinguish within-role redundancy from complementary roles. |
| Strategies | Aligned net-return/target-exposure correlations, active-session and entry overlap, holding behavior and fold/regime losses. Constant exposure gives undefined correlation, not diversity evidence. |

Use the metric contract's definitions. Low correlation alone is not an economic explanation;
do not manufacture noisy formulas to pass diversity checks. An IC gain alone does not prove
better strategy quality; no new global Sharpe record does not disprove useful risk research.

Allocate work across breadth, factor/strategy refinements and paired/robustness checks.
A round is a bounded set of questions followed by a portfolio review, not a stopping quota.
Choose the next batch by expected information, unresolved requirements and cost; adapt its
size and allocation as evidence changes. An unproductive round calls for diagnosis or a new
mechanism. Repeatedly tuning one leader cannot substitute for reviewing the shortlist, and
low-value filler trials are not exploration. Use the
[evaluation decision table](metrics-and-evaluation.md#evaluation-drives-the-next-experiment)
to record result IDs, changed values, diagnosis, next hypothesis and falsification condition.

## Readiness review

Before final freeze, reconcile coverage, every route's disposition, joint revisions, exact
role-qualified bindings, robustness and outstanding questions with saved results. Separate
mechanical checks from the Agent's judgment. For each dimension below record scope, exact
candidate/result IDs, evidence, counterevidence, limitations and conclusion; unknown is not met.

| Dimension | Evidence required |
| --- | --- |
| Mechanism and value | Interpretable causal hypothesis, role-qualified bindings, matched baseline and ablation evidence. Risk reduction with a return tradeoff is not proof of superior return. |
| Diversity and joint refinement | Distinct evaluated mechanisms, every shortlist review, factor-to-consumer revisions and strategy revisions; parameter-only variants do not establish breadth. |
| Robustness | Fold/regime results, parameter neighborhoods, period/trade concentration, direction stability and dependence-aware uncertainty; investigate isolated optima and conflicts. |
| Costs and feasibility | Gross/net and cash/order reconciliation, realistic execution, stress costs, turnover/exposure and sample support adequate for the particular claim. |
| Search effects | Complete trial/validation-look history and justified selection-aware assessment; calibrate claims to uncertainty, not the best point estimate. |
| Remaining exploration | Strongest concrete remaining hypotheses, expected information/cost and evidence for executing, parking or rejecting each. |
| Delivery | Reproducible numerical results, a complete continuous report, usable browser journeys and truthful source/capability outcomes. |

Continue while material testable questions or repairable implementation gaps remain. First
eligibility, one passing statistic, a report release or a preplanned batch is insufficient.
When ready, select using the predeclared policy over benefit, robustness, risk/cost and
complexity, with a stated tie break. Explain why more research is unlikely to materially
change the conclusion before accessing test; do not assert convergence without evidence.

## Final evaluation

Freeze selected factors, fitting policy, strategy, engines, costs, comparison policy,
evidence standard and data identities together, binding readiness to that exact bundle.
Predeclare any refit on pre-test data and exclude boundary-overlapping labels. Record the
exposure marker before retrieving test and bind its downloaded snapshot before calculating
metrics. Evaluate the frozen bundle once for both report sections, with only predeclared
benchmarks/scenarios. Test factor diagnostics describe that bundle; they cannot admit factors.

Judge the result against the frozen standard, including degradation, uncertainty, regimes
and practical tradeoffs: supported, not supported or inconclusive. Do not test replacements
until one looks good. Interrupted calculations may replay only the same frozen bundle with
known exposure. Identical result replay does not authorize another selection; post-reveal
corrections retain the original result and are diagnostic, not renewed confirmation.

A failed test does not automatically end research. Continue useful train/validation work
when material questions remain, preserving the failed bundle and labelling later candidates
exploratory. Their revisions cannot be confirmed on the exposed period. New confirmation
requires genuinely unused observations under a prospectively fixed protocol; do not silently
change dates, symbols or labels. Missing new confirmation does not excuse skipping useful work.

### Holdout trust boundary

Read `research.holdout_control` from the runtime manifest when supplied, and verify the
implemented boundary before describing its protections; the declaration is not enforcement.
The solo demo defaults to `holdout_control=protocol_only`. Deferred downloads, hashes and
ledgers reduce accidental leakage; an editable ledger or agent-authored environment cannot
prevent the author from reading/refetching data. Call it audited self-evaluation, not enforced
isolation. Historical stock selection and pretrained knowledge of market events/returns are
additional limitations; a single-stock result is not universal market alpha.

Independent isolation requires an evaluator outside the researcher's write/data authority.
The existing `factor_mining` Benchmark/bridge illustrates that boundary but is not mounted
automatically here. If enforced isolation is required, establish and test it or report it
unavailable; a UI lock icon or configuration label is not enforcement. New experiments have
independent work records, but repeated historical studies are not new market observations.

## Completion decision

Read a durable review before done_tool or a text-only ending. Record `decision`, claims and
support status, evidence/counterevidence by quality dimension, unresolved limitations,
remaining investigations and their dispositions. Link it from index.md. Choose:

- **Continue:** a material testable question, useful mechanism or repairable capability gap
  remains. State and execute the next factor/strategy experiment.
- **Complete with a supported strategy:** quality and delivery are satisfied, frozen final
  evidence supports the stated use and remaining investigations are not material.
- **Complete with a negative/inconclusive conclusion:** substantive diverse exploration,
  joint refinements and counterevidence support why no feasible remaining investigation
  would change the conclusion. Do not call the strategy successful or use "test failed"
  alone as that explanation.
- **Interrupted/blocked:** runtime/user resources, a user stop or an external prerequisite
  prevents useful progress. Save partial results, unmet requirements and the next hypothesis;
  resource exhaustion is not research completion.

Separate research completeness, strategy support, strict data qualification, product delivery
and capability evolution. The Agent must justify closure from evidence; counters, fixed
returns, lowered criteria and report-renderer success cannot establish research quality.
