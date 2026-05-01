---
name: population_genetics_skill
description: Solve population genetics problems requiring Hardy-Weinberg equilibrium calculations, allele/genotype frequency derivation, phenotype-frequency mapping, and multi-locus trait averaging — especially when multiple independent conditions (genotype of parent AND genotype of offspring) must be jointly satisfied to determine a phenotype outcome.
type: sop
version: 1.0.0
require_grad: true
---

# Population Genetics Skill

## Goal
Produce an exact numeric answer (allele frequency, genotype frequency, phenotype frequency, expected trait value, or population average) by correctly applying Hardy-Weinberg equilibrium, multi-condition phenotype rules, and any stated selection or mating constraints.

## When to Activate
- The problem gives allele or genotype frequencies and asks for a population average (height, weight, or other quantitative trait).
- The problem involves Hardy-Weinberg equilibrium (HWE) and asks to derive allele frequencies from genotype frequencies or vice versa.
- The problem involves a trait whose expression depends on **multiple simultaneous conditions** (e.g., parent genotype AND offspring genotype must both be satisfied).
- The problem involves dominance/recessiveness, allelic exclusion, or multi-locus interactions.
- Key vocabulary: "Hardy-Weinberg", "allele frequency", "genotype frequency", "homozygous", "heterozygous", "population bottleneck", "random mating", "average height/weight/trait", "phenotype", "dominant", "recessive".

## Common Failure Modes (Avoid These)
- **Single-condition error**: computing P(phenotype) from only ONE condition (e.g., only parent's genotype) when the phenotype actually requires MULTIPLE conditions to be jointly satisfied (e.g., parent's genotype AND child's genotype). Always enumerate ALL conditions an individual must satisfy to express a given phenotype.
- **q vs q² confusion**: the problem says "half the population is homozygous for allele 0" → this means q² = 0.5, so q = √0.5 ≈ 0.707, NOT q = 0.5. Always derive allele frequency from genotype frequency via HWE: q = √(P(aa)).
- **CRITICAL — False independence of parent and offspring genotype**: parent and offspring genotype are NOT independent — each parent contributes exactly one allele to the child. Under random mating, P(child genotype) is computed by Mendelian transmission from BOTH parents, not from the population frequency alone. If a parent's genotype is already known (or conditioned upon), you MUST use conditional Mendelian probabilities for the child's genotype — do NOT substitute the population-level q² or (1−q²). Example: if father is known to be non-0/0, P(child is 0/0) ≠ q². Instead: enumerate father's possible genotypes (0/1 or 1/1) weighted by their frequencies among non-0/0 fathers, pair with all possible mother genotypes under HWE, and compute P(child 0/0) by Mendelian cross.
- **Ignoring offspring's own genotype when it affects the phenotype**: if a child must be able to metabolize/consume a substance to benefit from it, and that ability is genotype-determined, then BOTH the availability condition (from parent) AND the consumption condition (from child's own genotype) must be checked.
- **Counting only one sex**: if a trait is sex-specific (e.g., only fathers provide milk), apply the genotype frequency to the correct sex only. Under HWE with autosomal loci, male and female frequencies are equal, so P(father is 0/0) = P(any individual is 0/0).
- **Forgetting selection contradicts HWE**: if the problem states HWE is maintained, do NOT apply survivorship selection models that would change allele frequencies.

## SOP — 3 Phases

### Phase 1 — Parse Frequencies and Conditions

1. Identify allele labels and which allele is dominant/recessive.
2. Derive allele frequencies from the stated genotype frequencies using HWE:
   - If P(aa) = f is given → q = √f, p = 1 − √f.
   - If allele frequency q is given directly → P(aa) = q², P(Aa) = 2pq, P(AA) = p².
3. List ALL distinct genotypes and their frequencies. Verify they sum to 1.
4. List every condition that must be satisfied for each phenotypic outcome. For each condition, state whether it depends on:
   - The individual's own genotype,
   - A parent's genotype,
   - An environmental variable set by a parent,
   - Some combination of the above.

### Phase 2 — Compute Joint Probabilities

1. For phenotypes that require multiple conditions, determine whether the conditions are truly independent:
   - **Parent genotype and environmental variable set by parent** (e.g., whether milk is provided): independent of child's genotype only if the child's trait is determined solely by environment, not own genotype.
   - **Parent genotype and child's own genotype**: NOT independent — the parent transmits one allele to the child. Use conditional Mendelian transmission:
     - For each possible father genotype g_f (weighted by frequency among fathers in the relevant class) and each possible mother genotype g_m (weighted by HWE frequency), compute P(child genotype) by Mendelian cross.
     - P(child 0/0 | father non-0/0) must be computed this way — it will generally NOT equal q².
2. For each phenotypic class, compute the probability and the associated trait value.
3. Verify: Σ P(all phenotypic classes) = 1.

### Phase 3 — Compute Population Average and Round

1. Population average = Σ [P(class_i) × trait_value_i].
2. Apply the rounding/significant-figure requirement stated in the problem.
3. **Self-audit**: re-read every condition in the problem and confirm each one was used. Flag any condition that was never multiplied into the joint probability.

## Subtype Playbook

- **HWE frequency derivation**: given P(aa) = f, derive q = √f, p = 1−√f, P(Aa) = 2pq, P(AA) = p². Verify sum = 1.
- **Single-parent-determined trait** (e.g., only father's genotype determines availability of a resource): P(child benefits) = P(father is functional genotype). Average = P(benefit) × high_value + (1−P(benefit)) × low_value.
- **Two-condition trait** (parent provides resource AND child can use resource — child's OWN genotype matters): P(child benefits) is NOT simply (1−q²)². Parent and child genotypes are linked by Mendelian transmission. Correct approach:
  1. Split into father-genotype classes: P(father 0/0) = q², P(father 0/1) = 2pq, P(father 1/1) = p².
  2. No-milk families (father 0/0, prob=q²): all children get low_value (42 in).
  3. Milk families (father non-0/0, prob=1−q²): child benefits only if child is also non-0/0. Compute P(child 0/0 | father non-0/0) by Mendelian transmission:
     - Among non-0/0 fathers: P(father is 0/1 | non-0/0) = 2pq/(1−q²), P(father is 1/1 | non-0/0) = p²/(1−q²).
     - For each father genotype, cross with HWE mother genotype distribution to get P(child 0/0).
     - Result: P(child 0/0 | father non-0/0) = q·(q + p)/2 · ... (numerically: if q=√0.5, this ≈ 0.293, NOT 0.5).
  4. avg = q²×low + (1−q²)×[P(child 0/0|father non-0/0)×low + P(child non-0/0|father non-0/0)×high].
  **Worked example** (q=√0.5≈0.7071, low=42, high=54):
  P(child 0/0 | father non-0/0) ≈ 0.2929 → avg = 0.5×42 + 0.5×(0.2929×42 + 0.7071×54) = 21 + 0.5×(12.30+38.18) = 21+25.24 = **46.24**.
- **Multi-allele dominance**: if heterozygote is functionally equivalent to homozygous dominant (full dominance), group P(Aa) + P(AA) = 1−q² as the "functional" class.
- **Quantitative trait with three genotype classes**: avg = P(AA)×v_AA + P(Aa)×v_Aa + P(aa)×v_aa. Compute each term separately.
- **Population bottleneck + HWE**: bottleneck sets initial genotype frequencies; after random mating, HWE restores allele-frequency-derived genotype frequencies. If the problem says both "bottleneck set P(aa)=f" AND "population is in HWE", these are consistent only if q = √f.

## Output Rules
- Give the numeric answer to the precision specified in the problem (significant figures or decimal places).
- Show the intermediate table: allele frequencies → genotype frequencies → per-class probabilities → weighted sum.
- State explicitly which conditions were multiplied together and why.
