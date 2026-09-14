---
name: factor_strategy_research_skill
description: "Research stocks with one agent: acquire auditable data, jointly discover and refine diverse factors and multi-factor strategies, and publish one continuous evidence report."
version: "1.13.0"
type: worker
category: finance
requirements: [cpu]
metadata: {}
---

# Factor and strategy research

Jointly discover and refine multi-factor strategy hypotheses and the factors they need. The Agent owns
planning, allocation and stopping judgments; this skill supplies research methods and
evidence contracts. Factor formulas, strategy families, batch sizes, pool membership and
optimization methods remain open within the study's data and execution constraints.
Research the complete trading mechanism, including combination learning, conditional roles,
holding/exit and allocation. Numerical thresholds and evidence methods are agent-designed
research choices, not universal constants supplied by this skill.

## Reference map

Read the workflow first, then only the reference needed for the current operation. Resolve
paths from the skill loader; keep detailed records on disk, not duplicated in live context.

| Reference | Responsibility |
| --- | --- |
| [Research workflow](references/research-workflow.md) | Plan and versioned archives, joint research loop, final evaluation and completion judgment. |
| [Experiment integrity](references/experiment-integrity.md) | Executable train-window, fitted-row, parent/control, completion and final-access checks; read when building or repairing research actions. |
| [Data and environments](references/data-and-environments.md) | Local acquisition, source qualification, Connector/two Environment interfaces, concurrent execution and recovery. |
| [Factor expressions](references/factor-expressions.md) | Operators, expression-to-code compiler and causal DataFrame input/output. |
| [Metrics and evaluation](references/metrics-and-evaluation.md) | Numerical definitions, factor roles, joint evaluation, diagnostic comparisons and fixtures. |
| [Reports](references/reports.md) | JSON exports, adapter CLI/schema, chart definitions and one continuous visual report. |

## Research method

1. Read the task/study; predeclare the benefit, comparison standard and allowed tradeoffs.
   Distinguish the investable calendar/account path from statistical fold scoring using the
   [accounting scope contract](references/metrics-and-evaluation.md).
   Design a batch of complete multi-factor hypotheses with distinct information and policy
   mechanisms: each strategy's factors, roles, combination, fitting and holding rules.
   Maintain concise `index.md` progress and links; put detailed designs and reviews on disk.
2. Keep discovery, fitting, optimization and selection inside train, using agent-designed
   chronological rolling windows. Download and verify train through a native Connector;
   defer test until one final bundle is frozen. Preserve known historical exposure across
   sessions: reserved from this run does not mean project-wide unseen.
3. Establish the numerical path with both Environments, one shared worker allowance,
   causality/accounting fixtures and a real pilot. Verify native overlap and serial/concurrent
   parity; reuse valid checks for unchanged behavior. Manager/Runtime owns admission and
   lifecycle. Integrate the integrity helpers in actual actions and check their receipts.
   Add finalization safeguards before final-test access, not before broad research.
4. Use the workflow's [staged evaluation](references/research-workflow.md):
   screen broadly, then revise promising routes as soon as their own evidence is sufficient.
   A missing final qualification does not block exploratory revisions. Choose a diagnostic
   only when its answer changes the next edit, pool decision or readiness claim; avoid a full
   control matrix for rejected routes. Schedule useful factor/policy revisions and new
   mechanisms alongside ready evaluations. Analyze JSON; rounds and reports are checkpoints,
   not barriers. Make a retained route's main diagnosis select a discriminating comparison
   and subsequent keep/revise/park decision. Count tested research changes separately from
   implementation and controls.
   Separate marginal predictive value, conditional/group value and whole-policy contribution;
   do not require every context input to pass a standalone directional-IC gate. Preserve
   failed original claims when proposing new roles or mechanisms.
   Derive controls from actual parent settings, declare all changes, and reproduce the
   parent's decisions with interventions disabled before attributing an effect.
   For event-entry routes, use the metrics reference's executed opportunity diagnostics;
   marginal factor IC alone does not establish a profitable entry/holding policy.
5. At research reviews, decide whether the next experiment is worthwhile. Stop when the
   objective is supported, or repeated ineffective improvements and low expected value
   justify a negative/inconclusive conclusion. No universal patience/count/return gate or
   proof of exhaustive search is required. Use the workflow's final-test and completion
   rules, including a result-bound readiness verdict for every required criterion; publish
   the saved results as one continuous HTML/JS report. Delivery uses file
   and HTTP checks, without browser acceptance or an open-ended presentation review.
   Worker completion is durable without agent-side collection. Reconcile actual results
   after workers stop; report snapshots and round summaries cannot define what completed.

Factors and strategies have a many-to-many relationship: share exact factor versions across
strategies, and use four, five or more factors when the mechanism benefits. There is no fixed
upper count; formal candidates need at least two distinct, actually used factors. Choose
inputs by contribution and complexity, not a quota. Archive candidate/ablation/benchmark
roles; unused copies and diagnostic controls do not count as discoveries.

## Research-specific capability evidence

This skill supplies methods, not a prebuilt data Connector or backtesting Environment.
Use self_evolving_skill for capability authoring, repair, registration and verified reuse.
The two Environments may share utilities but must execute their distinct research operations.
Compare capabilities on fixtures and permitted research data, never the final test.

Compiling a factor is research work; improving a reusable operator or engine may address a
system gap. Runtime receipts establish provenance, not financial correctness or independent
approval. Report research completeness, strategy support, data qualification, delivery and
system evolution separately, with the actual test-access boundary disclosed.
