---
name: factor_strategy_research_skill
description: "Research stocks with one agent: acquire auditable data, jointly discover and refine diverse factors and strategies, and publish one continuous evidence report."
version: "1.5.2"
type: worker
category: finance
requirements: [cpu]
metadata: {}
---

# Factor and strategy research

Use this method to build research software and jointly mine/refine factors and strategies
from historical market data. It supplies methods, not a ready-made data Connector or
backtesting Environment. Implement and verify those capabilities through self_evolving_skill.

This skill owns research methods, domain plan guidance and evidence standards. The Agent
chooses and executes the next operation; the shared plan module supplies storage/context
rules. Use the workflow reference to design the actual plan and its supporting records.

## Reference map

Read the relevant section at each decision using the absolute skill path from the loader.
Each function has one reference; formulas and interface contracts have a single owner.

| Function and reference | When to read; contents |
| --- | --- |
| [Research workflow](references/research-workflow.md) | Before implementation, allocating experiments, final freeze or completion: index/plan records, frozen chronology, diverse joint exploration, evidence standard, final test and reasoned ending. |
| [Data and environments](references/data-and-environments.md) | Before data acquisition or capability implementation: provider feasibility, local download acceptance, public-data qualification, Connector and two native Environment contracts, recovery and readiness. |
| [Factor expressions](references/factor-expressions.md) | Before implementing a factor: operator table, expression compiler, causal DataFrame input/output, CLI and extending operations. |
| [Metrics and evaluation](references/metrics-and-evaluation.md) | Before implementing numerical calculations or interpreting results: role-specific qualification, factor/strategy metric definitions, diversity and paired comparisons, diagnostic decisions and numerical fixtures. |
| [Reports](references/reports.md) | Before result export or UI work: one continuous page, inventories/charts, report adapter CLI and schema, source-bound values, visual theme and browser acceptance. |

Keep index.md concise and retain detailed designs, trials and reviews under the shared plan
directory. Read them on demand instead of copying full records into live context.

## Research method

1. Read the study and the workflow's planning and protocol sections. Define an implementable
   research plan, domain records and prospective evidence standards before evaluating performance.
2. Make the first executable milestone a native Connector download to disk, reopened and
   verified for path/hash, nonempty OHLCV and session coverage. Freeze the permitted local
   train/validation snapshot. Repair acquisition before market trials or report polish;
   a probe, in-memory response or fixture is not a research dataset. Honor authorized public
   data with disclosed limitations and separate strict qualification; defer test acquisition.
3. Implement both research Environments with the frozen metric contract. Check deterministic
   fixtures and successful native real-data operations. Compile factor expressions into
   versioned DataFrame-to-DataFrame implementations. Before expanding search, complete one
   real factor evaluation and one executable strategy baseline, export their metrics/series
   and pass scripts/report.py check. This milestone does not replace admission or finalization.
4. Iterate both research responsibilities: discover mechanisms, revise factors and each
   strategy's exact bindings/rules, evaluate on train/validation, and review every shortlisted
   route. Diagnose saved numerical evidence, perform matched parent/candidate comparisons,
   and keep meaningful alternatives. Update plan records and the same continuous report.
5. Apply the workflow's readiness review before freezing one joint final-test bundle. Continue
   while material testable questions or repairable gaps remain. Completion needs evidenced
   judgment of quality, remaining exploration and deliverables, not a fixed return target,
   candidate count or patience limit. Failed tests stay visible; later research is exploratory.
   A justified negative conclusion is distinct from a supported strategy; resource exhaustion
   is interruption. Use the workflow's completion decision before any ending.

## Research-specific capability evidence

Use self_evolving_skill for shared authoring, repair and versioned verification. Read its
Connector/Environment references and templates before authoring; that skill owns the common
lifecycle. Define research-specific numerical expectations for its comparisons and reuse
checks: another training date interval for a connector, or an action/delayed-fill fixture
and a different real hypothesis for an engine. Final test cannot evaluate a capability candidate.

The two Environments have distinct research responsibilities, state and native consumer
calls, although they may share utilities. Wrappers around fixed reports, failure-only
interfaces or fixture-only calculators do not provide the required research capability.
Compiling a factor is research work; a verified reusable operator/compiler improvement may
address a demonstrated system gap. Generate additional entities only for such a need.

Runtime receipts establish provenance/lifecycle, not financial correctness or independent
approval. The same agent authors and evaluates this work. Declare the actual test-access
boundary and keep research quality, strategy support, source qualification, product delivery
and system evolution as separate evidenced outcomes.
