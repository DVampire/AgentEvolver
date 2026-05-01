---
name: bio_med_experimental_design_skill
description: Answer biology/medicine questions across four domains: (1) flow cytometry experimental design and control counting, (2) kinase/enzyme substrate motif matching, (3) lymphocyte receptor biology and scRNA-seq dual-chain mechanism analysis, (4) CAR T cell cytokine manufacturing effect prediction, (5) statistical genetics True/False claims about PRS and SNP heritability.
type: sop
version: 2.0.0
require_grad: true
---

# Bio / Medical Experimental Design Skill

## Goal
Produce a precise answer — a count, a single option letter, a subset of mechanism indices, or True/False — by identifying which biological domain the question belongs to and applying the corresponding domain-specific reasoning rules.

## SOP
1. Read the question and match it to one of the five domains below using the activation keywords.
2. Follow that domain's **SOP** and watch for its **Common Mistakes**.
3. Apply the Output Rules at the end.

---

## Domain 1: Flow Cytometry Experimental Design

### When to Activate
- The problem asks how many controls are required for a flow cytometry or cell-sorting experiment.
- The problem provides a channel count and asks for the minimum essential control set.
- Key vocabulary: single-stain, FMO, isotype, live-dead, bead-based, streptavidin beads, unstained.

### Common Mistakes
- Applying the full baseline list (live-dead + isotype + beads-only) uniformly — these are NOT essential for bead-based sorting where the unstained bead tube itself serves as the reference.
- Counting only channels without adding single-stain and FMO controls.

### SOP
1. Identify the labeling strategy: **bead-based** vs **antibody-stained cells**.
2. For bead-based: essential = 1 unstained + N single-stain + N FMO. Formula: **1 + 2N**. Live-dead, isotype, beads-only are not essential.
3. For antibody-stained cells: full baseline = unstained + live-dead + FMO per channel + single-stain per channel + beads-only + isotype. Expand by channel count and sum.
4. Confirm the answer is an integer.

---

## Domain 2: Kinase / Enzyme Substrate Motif Matching

### When to Activate
- The problem provides a peptide sequence or phosphorylation motif and asks which kinase family phosphorylates it.
- Key vocabulary: consensus sequence, R-X-X-S/T, CaMK, PKC, CK2, substrate motif.

### Common Mistakes
- Concluding from partial motif similarity without checking every residue position. "Similar but wrong" families are a common trap.

### SOP
1. Extract the exact motif from the problem.
2. Align residue-by-residue against each candidate family's consensus:
   - CaMK: R/K at −3 or −2, then X-X-S/T
   - PKC: basic residues flanking S/T, often R/K at −3 and +2
   - CK2: S/T followed by X-X-E/D (acidic at +3)
3. Rule out any family that fails at any residue. Select the unique match.

---

## Domain 3: Lymphocyte Receptor Biology & scRNA-seq Dual-Chain Analysis

### When to Activate
- The problem asks about BCR/TCR allelic exclusion, VDJ recombination, or which receptor chain combinations a single lymphocyte can express.
- The problem asks which subset of listed mechanisms contribute >1% to observing dual light/alpha chains in droplet-based scRNA-seq (e.g. 10x Genomics).
- Key vocabulary: allelic exclusion, VDJ, dual light chain, dual alpha chain, scRNA-seq V(D)J, naive B cell, naive T cell, BCR, TCR, doublet, ambient RNA.

### Common Mistakes
- Treating B cells and T cells identically — they have different allelic exclusion stringency and must be analyzed separately.
- For T cells, excluding mechanism 6 (surface-expressed, non-autoreactive, non-functional second α chain). Thymic positive selection only requires one α chain to pass MHC restriction; the second can remain on the surface but be non-functional — this is distinct from autoreactive (mechanism 5).
- Overestimating ambient RNA as a >1% contributor — it is not established as significant for naive B or T cells in 10x datasets.

### SOP

**For allelic exclusion / receptor combination questions:**
1. Apply exclusion rules per chain type:
   - BCR heavy chain: strict — one allele per cell.
   - BCR light chain: strict, but receptor editing can produce two light chain mRNAs.
   - TCR β-chain: strict allelic exclusion.
   - TCR α-chain: leaky — ~30% of T cells express two α-chain mRNAs; ~10% express both on the surface.
2. Enumerate permitted combinations based on which chains are strictly vs leakily excluded.

**For scRNA-seq >1% mechanism contributor questions:**
1. Identify the cell type (B cell or T cell) — rules differ.
2. For each listed mechanism, evaluate whether it exceeds 1% using cell-type-specific biology:
   - **B cells**: evaluate doublets (3–8% multiplet rate), non-productive first-rearrangement mRNA, and receptor editing as plausible. Evaluate ambient RNA and fully functional dual-surface inclusion against B-cell-specific frequencies.
   - **T cells**: leaky α-chain exclusion (~30% dual mRNA) makes mechanisms 3, 4, 5, and 6 all plausible at >1%. For mechanism 6: the second α chain can be surface-expressed and non-autoreactive yet still non-functional if positive selection was driven by the other chain — do NOT conflate with autoreactive (5). Evaluate ambient RNA critically against T-cell-specific data.
3. Include only mechanisms that pass the >1% threshold.

---

## Domain 4: CAR T Cell Cytokine Manufacturing

### When to Activate
- The problem asks to predict the effect of a manufacturing cytokine (IL-2, IL-7, IL-15, IL-21) on CAR T cell behavior — cytokine release, persistence, exhaustion, or phenotype.
- Key vocabulary: CAR T, IL-15, IL-7, IL-2, IL-21, manufacturing, cytokine release, memory phenotype, Tcm, Tscm, effector, exhaustion.

### Common Mistakes
- Assuming a cytokine that "enhances T cell function or persistence" will also increase acute cytokine release. These can point in opposite directions.
- Confusing long-term polyfunctionality (sustained output over repeated challenges) with acute cytokine release (single stimulation readout). Always identify which readout the question is asking about.

### SOP
1. Identify the differentiation state the cytokine promotes: effector (Teff/Tem) vs memory/stem-cell (Tcm/Tscm).
2. Reason through what that differentiation state implies for the specific readout asked:
   - Effector-differentiated cells respond explosively to stimulation — consider what this means for acute cytokine output.
   - Memory/stem-cell-like cells respond less explosively acutely but maintain long-term function — consider what this means for acute vs repeated-challenge readouts.
3. Apply this chain to each cytokine:
   - **IL-2**: drives effector differentiation — reason through Teff/Tem implications.
   - **IL-15** (±IL-7): drives memory/Tscm phenotype — reason through Tcm/Tscm implications compared to IL-2.
   - **IL-21**: similar memory-promoting shift as IL-15 — apply the same chain.

---

## Domain 5: Statistical Genetics — PRS & SNP Heritability

### When to Activate
- The problem makes a True/False claim about polygenic scores (PRS), SNP heritability (h²_SNP), or variance explained.
- The claim uses quantifiers like "necessarily", "always", "bounded by", or "can exceed".
- Key vocabulary: polygenic score, PRS, SNP heritability, h²_SNP, variance explained, R².

### Common Mistakes
- Confusing "bounded by ≤" with "necessarily strictly less than". The ≤ bound is achievable (when ρ = 1), so a "necessarily strictly less" claim is not always true.
- Ignoring that empirical PRS R² can exceed estimated h²_SNP when heritability estimates are downward-biased.

### SOP
1. Write out the mathematical relationship: R²_PRS = h²_SNP × ρ² ≤ h²_SNP.
2. Identify the exact quantifier in the claim ("necessarily", "always", "can exceed").
3. Check whether the equality case (ρ = 1) is achievable in principle — if yes, "necessarily strictly less than" is False.
4. Consider whether empirical exceptions (biased heritability estimates) affect the claim.
5. Answer True only if the claim holds in ALL cases; answer False if any valid counterexample exists.

---

## Output Rules
- Integer answers: bare numerals, no units.
- Multiple-choice answers: single uppercase letter.
- Mechanism index lists: comma-separated integers in ascending order, enclosed in parentheses as specified by the problem.
- True/False: exact word "True" or "False".
- Action/recommendation answers: a single imperative phrase, no preamble.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
