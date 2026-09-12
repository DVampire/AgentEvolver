---
name: factor_strategy_research_skill
description: "Run a solo two-stage stock research project: auditable data acquisition, causal factor discovery, strategy experiments and one continuous evidence report."
version: "1.3.0"
type: worker
category: finance
requirements: [cpu]
metadata: {}
---

# Factor and strategy research

Use this method when building research software and mining hypotheses from historical
market data. It supplies research and reporting methods; the reusable data Connector
and two research Environments are developed or improved during the task. Do not confuse
having these instructions with having implemented or verified those capabilities.

## Read at the relevant decision

- Before implementation: [planning and work records](references/planning.md).
- Before acquiring data: [sources and capability interfaces](references/data-and-environments.md).
- Before computing performance: [research protocol](references/research-protocol.md).
- Before freezing a submission or ending a run: the protocol's submission-readiness and research-lifecycle rules.
- Before implementing either engine or evaluating candidates: [metrics and evaluation contract](references/metrics-and-evaluation.md).
- Before report design: [report experience and acceptance](references/reports.md).
- Before the first result export: [report data adapter and executable checks](references/report-data.md).

Read referenced files using the absolute skill path returned by the loader. Keep the
shared index concise; retain detailed evidence as files and read it on demand.

## One agent, two research stages

1. Read the supplied study and discover the actual runtime capabilities. Use inspect_tool
   with only capability_type to list loaded names, then inspect an exact returned name.
   This demo starts without market connectors or research environments. Draft names are
   not registered capabilities; register a completed candidate before inspecting or calling
   that name. Repository implementations can inform a baseline, but read their source with
   Bash at observed paths rather than passing file/class names to inspect_tool.
2. Make local data acquisition the first executable milestone: author/register the Connector,
   invoke a native training-period query and verify its saved artifact from disk. A successful
   HTTP probe, schema inspection or in-memory response is not a downloaded dataset. Record
   the native call ID, actual path, SHA-256, bytes, symbol, interval, adjustment basis, row
   count and requested/observed dates. Check OHLCV values and exchange-session coverage.
   After the probe passes, acquire and freeze the permitted train/validation snapshot locally;
   final-test acquisition remains deferred until submission freeze. Engines must reopen this
   verified snapshot, not refetch data for each candidate. Until local data acceptance passes,
   repair acquisition and avoid market trials, research releases or elaborate report styling.
   Fix the provider/feed, calendar,
   adjustment policy, dates and data fingerprints in the research contract. If access or
   coverage fails, follow the recovery workflow in the sources reference before spending
   on downstream trials or reports. Keep real research pending; fixtures cannot replace it.
   Provider choice is open: a missing commercial API key is a reason to check suitable public
   access, not to declare the whole study impossible. Use self_evolving_skill for component
   generation, repair and versioned verification.
   Follow the supplied data_policy: authorized public-data research proceeds with explicit
   adjustment/volume limitations and separate strict qualification. Do not turn an unmet
   strict qualification into an unconditional research blocker. For remote read operations,
   prefer a genuinely read-only MCP method and result_mode: artifact in CONNECTOR.md; the
   framework saves the full response and returns a path/hash receipt. Read its result field
   with Bash for normalization instead of implementing unrestricted MCP filesystem writes.
3. Establish the frozen protocol and an append-only experiment ledger. Build deterministic
   small fixtures with hand-computed outcomes before researching real performance. These
   check engineering correctness, not the existence of profitable signals. Implement the
   metrics reference's definitions, null handling, aggregation and gate records in the
   environments; export their results for the report instead of recalculating them in the UI.
4. Develop or improve the data Connector and factor Environment against demonstrated baseline
   limitations. Download a real snapshot through the kept connector. Evaluate causal factors,
   retain rejected trials and admit only eligible, complementary versions. Publish the first
   continuous report with factor definitions, actual diagnostics and admission/rejection
   evidence; show strategy progress in the same document without exposing final-test data.
5. Develop or improve the strategy Environment. Start with simple baselines, then combine
   admitted factors. Evaluate training and bounded walk-forward validation, cost sensitivity
   and ablations. Add strategy rules, results and comparisons to the same report page.
   Update the workbench and the plan after meaningful experiments.
   Before expanding search, complete one real factor evaluation and one executable strategy
   baseline through the native interfaces, export actual values/series and run scripts/report.py
   check on the adapter manifest. This is a training/validation integration milestone, not a
   test reveal or permission to admit a failed factor. Then complete fold admission, strategy
   fitting, robustness and finalization before calling the environments research-ready.
6. Feed strategy failures and unmet submission requirements back into factor or strategy research.
   Give each return to stage one a diagnosis, evidence, a new hypothesis and a bounded budget.
   Read the exported metric/gate summaries and apply the metrics reference's diagnostic
   decision table. Record baseline/candidate changes and the decision with exact result IDs.
   A new report, renamed formula or parameter permutation is not progress by itself.
7. Apply the research protocol's submission-readiness check on saved validation results.
   Initial eligibility is only a shortlist. Continue research when readiness is unmet and
   useful work and budget remain. Once readiness and documented search closure pass, freeze
   one final factor/strategy/engine bundle. Evaluate final test once, with the predeclared
   scenarios and metrics. Publish the integrated page
   with both stages and all final gates continuously visible; no report routes or stage tabs.
   Preserve a failed/inconclusive attempt and keep the research objective unmet. Apply the
   lifecycle below to continue useful research; never reuse an exposed test as unseen.

## Capability evolution

Invoke self_evolving_skill and follow the shared evolution rules and current adoption
schemas. Read its Connector and Environment references, including templates, before
authoring either type. Preserve executed baseline evidence before registration. Reuse
correct existing code; improve a suitable component rather than adding an identical one.

Evaluate each exact registered version on a comparison and a different reuse/regression
case, with actual numerical expectations, failures and measured cost. For example, replay
a connector on another training-only date interval; compare an engine on a deterministic
split/dividend or delayed-fill fixture and a different real research hypothesis. Keep only
passing candidates, then invoke them directly on subsequent real research operations and
record use. Do not use final test to evaluate a capability candidate.

The two Environments need distinct research responsibilities, state and native consumer
calls, although they can share numerical utilities. A pair of wrappers over the same
fixed report does not establish this. Additional skills or tools are appropriate when
repeated research exposes a reusable limitation; do not generate entities just to raise a count.

Runtime receipts verify provenance and lifecycle, not financial correctness. The same
agent authors and evaluates this work. Report the limits of that evidence and the exact
test access controls. Never label a protocol-only holdout as an isolated evaluator.
Verify the engines' successful numerical paths through their research interfaces as well as
their rejection paths. A failure-reporting wrapper or fixture-only calculator leaves the
market-research capability incomplete; see the sources reference for readiness checks.

## Research lifecycle

Successful research requires the task's real-data final-test criteria and all requested
deliverables. A failed candidate, failed final attempt or report release does not by itself
end the research task. Follow the protocol's research-lifecycle decision table: continue
budgeted factor/strategy work or repair a demonstrated capability gap while useful work
remains. Keep attempt outcome, research activity and overall acceptance separate in the plan
and report. After test exposure, preserve the frozen attempt and mark further research as
exploratory; fresh confirmation needs genuinely unused data under a new predeclared protocol.

End unsuccessfully only with evidence of an exhausted applicable budget/patience rule, a user
stop, or a concrete prerequisite with no useful in-scope work remaining. Awaiting fresh test
data can block confirmation without immediately blocking research. Do not keep a process
busy with repeated checks when no progress is possible. Record the actual stop reason,
remaining budget, unmet criteria, next hypothesis and exact resume dependency. Repairable
engine defects and missing connector code are work, not external-access blockers. Never
relax criteria, hide unsuccessful trials or purchase data to manufacture a passing run.
