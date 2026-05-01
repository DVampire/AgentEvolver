---
name: isotope_carbon_tracing_skill
description: Trace isotope-labeled carbons (13C, 14C) through metabolic pathways (glycolysis, TCA cycle, gluconeogenesis, pentose phosphate pathway) to determine how many labeled CO2 molecules are released, or which product carbons carry the label; also answer methodology questions about 13C metabolic flux analysis (13C MFA) requirements and design.
type: sop
version: 1.0.0
require_grad: true
---

# Isotope Carbon Tracing Skill

## Goal
Given a molecule with specific carbon positions labeled (e.g., 1,4-¹³C glucose), trace each labeled carbon through the specified metabolic pathway(s) and determine the labeling pattern of the products or the number of labeled CO₂ molecules released.

## When to Activate
- The problem specifies a carbon-labeled substrate (e.g., "1-¹³C pyruvate", "2,3-¹⁴C glucose", "1,4-¹³C glucose").
- The question asks how many labeled CO₂ are released, or which carbons of a product are labeled.
- The pathway involves glycolysis, pyruvate decarboxylation, TCA cycle, gluconeogenesis, or pentose phosphate pathway.
- Key vocabulary: ¹³C, ¹⁴C, isotope label, labeled CO₂, carbon tracing, glycolysis, TCA, Krebs cycle, pyruvate decarboxylation.

## Common Mistakes
- **Stopping at glycolysis**: glycolysis itself releases no CO₂ — CO₂ is released at pyruvate decarboxylation and TCA cycle decarboxylation steps. Always continue tracing to the relevant decarboxylation step.
- **Missing the DHAP→G3P isomerization**: when aldolase cleaves fructose-1,6-bisphosphate, DHAP (C1–C3 of glucose) is isomerized to G3P before continuing. This reverses the carbon order within the three-carbon fragment — C1 of glucose ends up at C3 of pyruvate (methyl end, not released as CO₂), and C3 of glucose ends up at C1 of pyruvate (carboxyl end, released as CO₂ in pyruvate decarboxylation).
- **Treating symmetric TCA intermediates as asymmetric**: succinate and fumarate are symmetric molecules — the two ends are indistinguishable, so label becomes equally distributed between the two carboxyl groups after this step.
- **Double-counting**: glucose yields two three-carbon units; each produces one pyruvate. Count contributions from both halves separately, then sum.
- **Confusing carbon numbering conventions**: glucose C1 is the aldehyde carbon (anomeric carbon); numbering runs C1→C6 toward the CH₂OH end. Verify orientation before tracing.

## SOP

### Phase 1 — Map the Labeled Positions
1. Write out the full carbon skeleton of the starting molecule and number each carbon (C1, C2, … Cn).
2. Mark which positions carry the isotope label (e.g., C1 and C4 for 1,4-¹³C glucose).
3. Confirm the numbering convention matches the IUPAC name given.

### Phase 2 — Trace Through Each Pathway Step
Work step by step through the pathway. At each enzymatic step, track what happens to every labeled carbon:

**Glycolysis key steps to watch:**
- **Phosphoglucose isomerase** (glucose-6-P → fructose-6-P): C1↔C2 positional shift — re-number accordingly.
- **Aldolase** (fructose-1,6-bisP → DHAP + G3P): the molecule splits. DHAP gets C1–C3 of fructose; G3P gets C4–C6 of fructose. Write out which labeled carbons go into which fragment.
- **Triose phosphate isomerase** (DHAP → G3P): DHAP is converted to G3P with carbon order reversal — C1 of DHAP becomes C3 of G3P, C3 of DHAP becomes C1 of G3P.
- After isomerization, **both G3P molecules** continue through the rest of glycolysis to pyruvate.

**Pyruvate carbon positions:**
- C1 of pyruvate = carboxyl group → **released as CO₂** during pyruvate decarboxylation (pyruvate → acetyl-CoA).
- C2 of pyruvate = carbonyl (keto) group → enters acetyl-CoA as C2.
- C3 of pyruvate = methyl group → enters acetyl-CoA as C1.

**TCA cycle key decarboxylation steps:**
- **Pyruvate dehydrogenase**: C1 of pyruvate → CO₂; C2+C3 → acetyl-CoA (C1 and C2 of acetyl group).
- **Isocitrate dehydrogenase**: releases CO₂ from C1 of isocitrate (derived from oxaloacetate C4).
- **α-Ketoglutarate dehydrogenase**: releases CO₂ from C1 of α-ketoglutarate (derived from oxaloacetate C1 or acetyl-CoA C1 depending on turn).
- **Symmetry point**: at succinate/fumarate, label is scrambled equally between C1/C4 and C2/C3 positions.

### Phase 3 — Count Labeled CO₂
1. For each labeled carbon, determine if it ends up at a position that is released as CO₂ in the pathway step(s) specified by the question.
2. Sum contributions from all labeled carbons across both halves of glucose (if the full pathway through both pyruvates is included).
3. Account for symmetry dilution at succinate/fumarate if the question involves TCA cycle beyond α-ketoglutarate.

### Phase 4 — State the Answer
- Report the integer count of labeled CO₂ molecules released per molecule of starting substrate.
- If the question asks about labeling pattern of a product, report which carbon positions of the product carry the label.

## Pathway Carbon Map Reference

**Glucose → Pyruvate (via glycolysis):**
```
Glucose C1 → Fructose C1 → DHAP C1 → G3P C3 → Pyruvate C3 (methyl, NOT CO₂)
Glucose C2 → Fructose C2 → DHAP C2 → G3P C2 → Pyruvate C2 (carbonyl, NOT CO₂)
Glucose C3 → Fructose C3 → DHAP C3 → G3P C1 → Pyruvate C1 (carboxyl → CO₂ at PDH)
Glucose C4 → Fructose C4 → G3P C1  → Pyruvate C1 (carboxyl → CO₂ at PDH)
Glucose C5 → Fructose C5 → G3P C2  → Pyruvate C2 (carbonyl, NOT CO₂)
Glucose C6 → Fructose C6 → G3P C3  → Pyruvate C3 (methyl, NOT CO₂)
```

Use this map to look up where each labeled glucose carbon ends up in pyruvate, then determine if that pyruvate position is released as CO₂ at the step the question asks about.

---

## Domain 2: ¹³C Metabolic Flux Analysis (¹³C MFA) Methodology

### When to Activate
- The problem asks which information is **required** (or not required) to perform ¹³C MFA at steady state.
- The problem asks about the design, assumptions, or inputs of ¹³C MFA experiments.
- Key vocabulary: metabolic flux analysis, MFA, flux, steady state, isotope labeling pattern, stoichiometry, biomass composition.

### Common Mistakes
- **Conflating experimental measurements with required prior inputs**: isotope labeling patterns of intermediates are the **experimental output/measurement** of ¹³C MFA, not a required input. Do not count them as prior information needed to run the analysis.
- **Including enzyme kinetics**: ¹³C MFA is a stoichiometric/data-driven method — it does not require enzyme kinetic parameters. Kinetics are needed for kinetic modeling, not MFA.
- **Including regulatory networks**: steady-state MFA makes no assumptions about regulatory mechanisms; only stoichiometry and biomass drain matter.
- **Including maximum cell density**: absolute cell numbers are irrelevant; MFA works with specific fluxes (per unit biomass), not total cell counts.

### SOP
1. Establish the nature of ¹³C MFA as a method:
   - It is a **stoichiometric, data-driven** method that infers intracellular fluxes
     by fitting a metabolic network model to experimentally measured isotope data.
   - It operates at **steady state**, so it does not model transient dynamics or
     require knowledge of how the system changes over time.

2. For each candidate item, apply the following two-question test:
   - **Question A — Is it a prior input?** Would this information need to be known
     *before* designing or running the experiment? If not, it is experimental output,
     not a prior input.
   - **Question B — Is it required by the MFA method itself?** Does the mathematical
     formulation of steady-state ¹³C MFA actually use this information to set up or
     solve the flux balance equations? If the method can be fully formulated and
     solved without it, it is not required.
   - An item is only required if it passes BOTH questions.

3. Apply the method's nature to guide classification:
   - Information that defines the **reaction network structure or carbon atom
     transitions** is needed to set up the model equations.
   - Information that defines **carbon drains from central metabolism** (e.g., into
     biomass macromolecule fractions) is needed to close the mass balances.
   - Information that is **collected as experimental output** during the labeling
     experiment is measured data, not a prior input — even if the method depends
     on it heavily for fitting.
   - Information specific to **kinetic or dynamic modeling** (rate constants, enzyme
     parameters, regulatory interactions) is not used in stoichiometric steady-state MFA.
   - **Absolute quantities** (e.g., total cell count) are not required; MFA works
     with specific fluxes normalized per unit biomass.

4. Count the items that pass both questions in step 2 and state the reasoning for
   each inclusion and exclusion explicitly.

---

## Output Rules
- Domain 1 (carbon tracing): bare integer count of labeled CO₂, with per-carbon tracing table shown first.
- Domain 2 (MFA methodology): bare integer count of required items, with explicit include/exclude reasoning for each listed item.
