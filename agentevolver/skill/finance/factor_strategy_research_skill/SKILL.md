---
name: factor_strategy_research_skill
description: "Run a solo two-stage stock research project: auditable data acquisition, causal factor discovery, strategy experiments and interactive evidence reports."
version: "1.0.1"
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
- Before report design: [report experience and acceptance](references/reports.md).

Read referenced files using the absolute skill path returned by the loader. Keep the
shared index concise; retain detailed evidence as files and read it on demand.

## One agent, two research stages

1. Read the supplied study and discover the actual runtime capabilities. Use inspect_tool
   with only capability_type to list loaded names, then inspect an exact returned name.
   This demo starts without market connectors or research environments. Draft names are
   not registered capabilities; register a completed candidate before inspecting or calling
   that name. Repository implementations can inform a baseline, but read their source with
   Bash at observed paths rather than passing file/class names to inspect_tool.
2. Establish data feasibility with a training-period query. Fix the provider/feed, calendar,
   adjustment policy, dates and data fingerprints in the research contract. Stop honestly
   if credentials or coverage cannot satisfy it; do not substitute synthetic performance.
3. Establish the frozen protocol and an append-only experiment ledger. Build deterministic
   small fixtures with hand-computed outcomes before researching real performance. These
   check engineering correctness, not the existence of profitable signals.
4. Develop or improve the data Connector and factor Environment against demonstrated baseline
   limitations. Download a real snapshot through the kept connector. Evaluate causal factors,
   retain rejected trials and admit only eligible, complementary versions. Publish the first
   interactive factor report without exposing final-test data.
5. Develop or improve the strategy Environment. Start with simple baselines, then combine
   admitted factors. Evaluate training and bounded walk-forward validation, cost sensitivity
   and ablations. Update the workbench and the plan after meaningful experiments.
6. Feed strategy failures back into factor research before freezing the final submission.
   Give each return to stage one a diagnosis, evidence, a new hypothesis and a bounded budget.
   A new report, renamed formula or parameter permutation is not progress by itself.
7. After validation eligibility, freeze one final factor/strategy/engine bundle. Evaluate
   final test once, with the predeclared scenarios and metrics. Release both final reports.
   Preserve a failed/inconclusive result; do not mine the test until something passes.

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

## Stop conditions

Successful research requires the task's real-data final-test criteria and all requested
deliverables. Budget exhaustion, no validation progress, unavailable data, a defective
engine, insufficient observations and a failed final test each require an explicit
non-success outcome. Preserve detailed reasons and a reproduction/resume path. Never
relax criteria, hide unsuccessful trials or purchase data to manufacture a passing run.
