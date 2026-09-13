---
name: factor_strategy_research_skill
description: "Research stocks with one agent: acquire auditable data, jointly discover and refine diverse factors and multi-factor strategies, and publish one continuous evidence report."
version: "1.8.1"
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

## Reference map

Read the workflow first, then only the reference needed for the current operation. Resolve
paths from the skill loader; keep detailed records on disk, not duplicated in live context.

| Reference | Responsibility |
| --- | --- |
| [Research workflow](references/research-workflow.md) | Plan and versioned archives, joint research loop, final evaluation and completion judgment. |
| [Data and environments](references/data-and-environments.md) | Local acquisition, source qualification, Connector/two Environment interfaces, concurrent execution and recovery. |
| [Factor expressions](references/factor-expressions.md) | Operators, expression-to-code compiler and causal DataFrame input/output. |
| [Metrics and evaluation](references/metrics-and-evaluation.md) | Numerical definitions, factor roles, joint evaluation, diagnostic comparisons and fixtures. |
| [Reports](references/reports.md) | JSON exports, adapter CLI/schema, chart definitions and one continuous visual report. |

## Research method

1. Read the task/study, fix causal evaluation assumptions, and design an initial batch of
   complete multi-factor hypotheses: each strategy's factors, roles, combination and rules.
   Maintain concise `index.md` progress and links; put detailed designs and reviews on disk.
2. Download train/validation data through a native Connector, reopen and verify the local
   snapshot. Disclose authorized public-data limitations; defer final-test acquisition.
3. Build both numerical Environments with configurable bounded concurrency and one shared
   worker allowance. Prefer call-scoped trial instances with declared input/output paths;
   let Manager/Runtime handle admission and lifecycle. Before scaling,
   verify actual overlapping execution and serial/concurrent result parity through their native
   interfaces, alongside causality/accounting fixtures and a representative real-data pilot.
   Add finalization checks before final-test access; render reports at research milestones.
4. Propose strategies and their factors together → batch evaluate → select a diverse working
   pool → diagnose and revise factors, policies or both. Add new mechanisms when useful;
   run independent factor diagnostics and strategies concurrently once their inputs are ready.
   Choose batch sizes and effort from evidence. Analyze saved JSON, not HTML or screenshots.
5. At research reviews, decide whether the next experiment is worthwhile. Stop when the
   objective is supported, or repeated ineffective improvements and low expected value
   justify a negative/inconclusive conclusion. No universal patience/count/return gate or
   proof of exhaustive search is required. Use the workflow's final-test and completion
   rules; publish the saved results as one continuous HTML/JS report.

Formal candidates combine at least two distinct, actually used factors. The Agent chooses
how many and how they interact; unused inputs and duplicate formulas do not establish a
multi-factor mechanism. Archive explicit candidate/ablation/benchmark roles. Controls explain
contribution and are never counted, shortlisted or submitted as discoveries.

## Research-specific capability evidence

This skill supplies methods, not a prebuilt data Connector or backtesting Environment.
Use self_evolving_skill for capability authoring, repair, registration and verified reuse.
The two Environments may share utilities but must execute their distinct research operations.
Compare capabilities on fixtures and permitted research data, never the final test.

Compiling a factor is research work; improving a reusable operator or engine may address a
system gap. Runtime receipts establish provenance, not financial correctness or independent
approval. Report research completeness, strategy support, data qualification, delivery and
system evolution separately, with the actual test-access boundary disclosed.
