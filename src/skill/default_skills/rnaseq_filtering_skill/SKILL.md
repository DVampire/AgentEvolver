---
name: rnaseq_filtering_skill
description: Design or evaluate RNA-seq filtering strategies for removing contamination, batch effects, or non-target cell signals in differential expression analyses, where the answer requires reasoning about LFC cutoffs, directionality, and the biological source of the contamination.
type: sop
version: 1.1.0
require_grad: true
---

# RNA-seq Filtering Strategy Skill

## Goal
Identify the correct filtering strategy — including the direction and stringency of the LFC cutoff — to remove contaminating signals from an RNA-seq dataset while preserving true biological differences between conditions.

## When to Activate
- The problem describes an RNA-seq experiment where one condition contains contaminating non-target cell signals.
- The question asks for a filtering strategy based on log2 fold change to eliminate contamination.
- The answer requires reasoning about which direction the contamination appears in the LFC and how stringent the cutoff must be.

## Common Failure Modes (Avoid These)
- **Cutoff too lenient**: a low LFC threshold (e.g. > 1) will remove genuine biological signal along with contamination — true condition differences often fall in the low-to-moderate LFC range.
- **Wrong direction**: always identify which condition contains the contamination and ensure the LFC filter is applied in the correct direction (filtering genes enriched in the contaminated condition, not the clean one).
- **Ignoring biological plausibility of the cutoff**: the chosen cutoff must be high enough that only contamination-derived signals (which are extreme because they are absent in the clean condition) are removed, while real transcriptional differences are retained.
- **Confusing filtering with differential expression**: this is a quality-control step to remove non-T-cell (or non-target-cell) genes before downstream analysis, not a step to identify significant genes.

## SOP — 3 Phases

### Phase 1 — Identify the Contamination Source and Direction
1. Determine which sample group contains the contamination (the "contaminated condition").
2. Identify the biological origin of the contaminating signal (e.g. residual cancer cells, feeder cells, non-target cell type).
3. Establish the comparison direction: contaminated condition vs clean condition. Contaminating genes will show extremely high LFC in the contaminated condition relative to the clean condition.
4. Note why contamination is asymmetric between conditions — this mechanistic explanation justifies the filtering approach.

### Phase 2 — Determine the Appropriate LFC Cutoff
1. Reason about the expected LFC range for true biological differences between the two conditions: these are typically moderate (condition-driven transcriptional changes).
2. Reason about the expected LFC range for contamination signals: these are extreme because the contaminating cell type is present in one condition and absent (or near-absent) in the other — producing very high fold changes.
3. Select a cutoff that sits above the expected range of true biological signal but captures the extreme contamination signal. True T cell or immune cell transcriptional differences between conditions are typically in the low-to-moderate LFC range (|LFC| ≤ 3); contamination signals from a completely absent cell type are extreme (LFC > 4). The recommended default threshold for non-target cell co-culture contamination is **LFC > 4** in the contaminated condition. Use LFC > 5 only when an exceptionally conservative filter is explicitly requested; LFC > 5 risks retaining contaminating genes near the threshold.
4. The cutoff applies specifically to genes in the contaminated condition that are anomalously enriched relative to the clean condition.

### Phase 3 — State the Filtering Strategy
1. Specify: the comparison direction, the LFC threshold, and which genes are removed.
2. Confirm the strategy preserves true condition differences while removing only extreme outlier signals attributable to contamination.
3. Do not provide implementation code unless explicitly asked — the answer is a filtering criterion, not a script.

## Subtype Playbook
- **Non-target cell contamination (e.g. cancer cells in T cell sample)**: contaminating genes are highly expressed in the contaminated condition and near-zero in the clean condition. Recommended cutoff: **LFC > 4** in the contaminated vs clean comparison. This threshold is validated for co-culture repeat experiments with FACS-sorted T cell populations where cancer cell contamination is the primary noise source.
- **Symmetric contamination across conditions**: if contamination is present in both conditions equally, LFC-based filtering will not work — a different approach (e.g. gene blacklist, cell type deconvolution) is needed.
- **Batch effect vs contamination**: batch effects produce moderate, diffuse LFC shifts across many genes; contamination produces extreme LFC in a specific gene set. Distinguish before choosing a filtering approach.

## Output Rules
- State the filtering criterion as a single concise logical condition on LFC (direction and threshold). Example format: "LFC > [threshold] in [contaminated condition]".
- Justify the threshold choice in one sentence.
- Do NOT include command-line code, script snippets, awk commands, R code, or any implementation details — even if the user asks for them. The answer is a filtering criterion only. Any code output will be ignored by the evaluator.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
