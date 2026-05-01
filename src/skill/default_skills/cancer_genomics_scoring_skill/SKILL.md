---
name: cancer_genomics_scoring_skill
description: Compute quantitative clonal expansion scores or similar weighted-sum metrics for tumors based on somatic copy number variations (CNVs), gene weights, and regulatory modifiers such as repressors or activators.
type: sop
version: 1.1.0
require_grad: true
---

# Cancer Genomics Scoring Skill

## Goal
Produce a single numeric score by applying per-gene weights to CNV events, then applying any regulatory modifiers stated in the problem.

## When to Activate
- The problem provides a list of chromosomal CNV events (gain/loss of N copies) with affected genes.
- Each gene has a weight per additional copy (oncogene) or per lost copy (tumor suppressor).
- The problem asks for a total score (clonal expansion score, tumorigenic score, or similar).
- Additional regulatory modifiers (repressors, activators overexpressed on specific chromosomes) are stated.

## Common Failure Modes (Avoid These)
- **Applying a weight in the wrong direction**: oncogene weights apply only to gain events; tumor suppressor weights apply only to loss events. A tumor suppressor on a gained chromosome contributes 0, and an oncogene on a lost chromosome contributes 0.
- **Misreading "normal copy number"**: the phrase "gene X has normal copy number" describes the *baseline* diploid state of the individual gene, but if the chromosome carrying it has a gain or loss event, the gene IS affected by that CNV. The chromosome-level gain/loss still applies to the gene's weight calculation. "Normal copy number" does NOT mean the gene contributes 0 to the score — it means the reference copy number before the chromosomal event is 2 (diploid).
- **Ignoring regulatory modifiers**: a repressor overexpressed on a chromosome neutralizes or subtracts the tumor suppressor contribution on that chromosome; read the modifier statement carefully to determine which genes and which chromosomes are affected.
- **Double-counting modifier effects**: apply each modifier exactly once per affected gene per chromosome.
- **Assuming "normal copy number" means zero contribution**: "normal copy number" describes the baseline diploid state, not absence of CNV effect. The chromosome-level gain/loss still applies. If a chromosome has a gain event and carries a gene with "normal copy number", that gene's oncogene weight still contributes (gain direction applies).

## SOP — 3 Phases

### Phase 1 — Parse All Inputs
1. For each chromosome, record: direction (gain/loss), number of copies changed, and affected genes with their roles (oncogene or tumor suppressor).
2. For each gene, record: weight and direction it applies (oncogene → gain; tumor suppressor → loss).
3. List all regulatory modifiers and which chromosomes/genes they affect.

### Phase 2 — Compute Per-Chromosome Contributions
For each chromosome:
1. Identify the CNV direction (gain or loss).
2. For each affected gene:
   - If **gain** and gene is an **oncogene**: contribution = copies gained × weight.
   - If **loss** and gene is a **tumor suppressor**: contribution = copies lost × weight (weight is negative, so this reduces the score).
   - Otherwise: contribution = 0. Do not apply an oncogene weight to a loss, or a tumor suppressor weight to a gain.
3. Record the chromosome subtotal.

### Phase 3 — Apply Regulatory Modifiers and Sum
1. For each stated repressor/activator, identify the affected chromosomes and the tumor suppressor genes on those chromosomes.
2. **Repressor effect**: for each tumor suppressor on an affected chromosome that contributed 0 in Phase 2 (i.e. the TS was on a gain chromosome so its loss-direction weight was not applied), the repressor independently activates that penalty — add the TS weight once as a separate term. For tumor suppressors that already contributed their full weight in Phase 2 (i.e. TS on a loss chromosome), the repressor effect is already captured and no additional term is added.
   - Concretely: repressor penalty applies to TS genes whose Phase 2 contribution was 0 due to direction mismatch.
3. Sum all Phase 2 chromosome subtotals plus all Phase 3 repressor penalty terms to produce the final score.
4. State the arithmetic explicitly before giving the final answer.

## Subtype Playbook
- **Repressor overexpressed on chromosome**: for each tumor suppressor on that chromosome, add its weight as an independent penalty (even if the TS contributed 0 in Phase 2). Apply once per TS per chromosome.
- **Activator overexpressed on chromosome**: amplifies the oncogene contribution; multiply or add as the problem specifies.
- **Gene with "normal copy number" note**: treat as baseline; the chromosome-level CNV still applies to that gene's weight calculation.

## Output Rules
- Commit to a single numeric answer.
- Show the full arithmetic (per-chromosome breakdown + modifier adjustments) before stating the final score.
- Round only if the problem specifies precision; otherwise report the exact value.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
