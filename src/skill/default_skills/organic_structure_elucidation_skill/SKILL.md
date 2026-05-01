---
name: organic_structure_elucidation_skill
description: Determine the structure of an unknown organic compound from combustion analysis data, spectroscopic clues (IR, NMR, MS), wet chemical tests (Tollens, FeCl₃, Lucas, etc.), and degradation/reaction products (ozonolysis fragments, HBr derivatives). The molecular formula from code is treated as ground truth and no structural candidate with a different formula is accepted.
type: sop
version: 1.0.0
require_grad: true
---

# Organic Structure Elucidation Skill

## Goal
Identify the unique structure (IUPAC name or structural formula) of an unknown organic compound by conjunctively satisfying all analytical constraints. The molecular formula derived from combustion analysis code is the primary anchor; no structural candidate may deviate from it.

## When to Activate
- The problem gives combustion analysis data (masses of CO₂, H₂O, N₂, etc.) and asks for the structure or IUPAC name of the unknown.
- The problem provides a combination of: IR/NMR/MS data + chemical test results (Tollens, FeCl₃, Lucas, Hinsberg, Baeyer, etc.) + degradation products (ozonolysis, permanganate oxidation, hydrolysis).
- The problem states the molar mass (cryoscopic, osmometric, mass spectrometry) along with an error bound.
- The problem provides fragment structures (ozonolysis products, hydrolysis products) and asks you to reconstruct the parent.

## Common Failure Modes (Avoid These)
- **Accepting a structural candidate with a different molecular formula than the code result.** If code gives C₁₀H₁₂O, no candidate with C₁₀H₁₂O₂ is acceptable, even if it fits more qualitative constraints.
- **Overriding the code-computed molecular formula** because a structural argument is "more defensible". The formula is a hard arithmetic constraint, not a soft guess.
- **Ignoring the symmetry constraint on HBr/monobromination products.** The number of distinct monobromination products and their chirality is a strong structural filter — enumerate systematically.
- **Forgetting to verify degrees of unsaturation (DoU).** DoU = (2C + 2 + N - H - X) / 2 for CₙHₘ type. Each ring and each double bond consumes one DoU. This must balance.
- **Assuming ozonolysis fragments uniquely reconstruct the parent without checking symmetry.** If only one ozonolysis product is observed, the parent may be symmetric (two identical fragments) or the fragments may be reused twice.

## SOP — 5 Phases

### Phase 1 — Molecular Formula (Code-Mandatory)
1. Write a Python script to compute the molecular formula from combustion data:
   - From mass(CO₂): moles C = mass(CO₂) / 44.010
   - From mass(H₂O): moles H = 2 × mass(H₂O) / 18.015
   - If mass(N₂) given: moles N = 2 × mass(N₂) / 28.014
   - Mass of O (if any) = sample_mass − mass_C − mass_H − mass_N
   - Compute C:H:N:O molar ratios; divide by smallest; find smallest integer multiplier consistent with the given molar mass (±error %).
2. Output a **ranked list** of candidate molecular formulas within the molar mass error range (typically ±10%).
3. The formula with the lowest deviation from the nominal molar mass and best fit to the combustion ratios is the **locked formula**. No subsequent step may override it.
4. Compute the degree of unsaturation (DoU) for the locked formula.

### Phase 2 — Constraint Table
Build a table listing every constraint and what structural feature it implies:

| Constraint | Structural implication |
|---|---|
| IR: -OH present | alcohol, phenol, carboxylic acid, enol, or chelated enol |
| IR: no C≡C | no alkyne, no nitrile |
| Tollens positive | aldehyde (or alpha-hydroxy ketone) |
| FeCl₃ red/violet | enol, phenol, or beta-diketone/keto-enol |
| Na/NaOH reactive | acidic O-H (phenol, enol, carboxylic acid) |
| HI reduction → alkane | all oxygens present as C-O bonds (ether, alcohol, carbonyl) that are reduced to CH₂/CH₃ |
| Ozonolysis → single product | parent is symmetric OR one ozonolysis site gives two identical fragments |
| n monobrominated derivatives, k chiral | counts the symmetry-distinct CH positions and their chirality environments |

### Phase 3 — Fragment Reconstruction
1. If ozonolysis fragments are given: determine the oxidation state of each fragment oxygen (aldehyde = one C=O, ketone = one C=O internal, carboxylic = two C=O). Reconstruct the parent alkene by replacing each C=O in the fragment with =CH- and connecting.
2. If a reduction product (HI reduction) is given: the reduced product tells you the carbon skeleton after removing all oxygens. Use this to fix the carbon chain/ring.
3. Cross-check: the reconstructed fragment + DoU from Phase 1 must be consistent (each ring or double bond accounts for one DoU).
4. For monobromination symmetry analysis: enumerate all distinct carbon positions on the candidate skeleton; for each, determine if the resulting mono-bromo compound would be chiral; count how many distinct (position, chirality) pairs. Must match the problem statement.

### Phase 4 — Candidate Verification
1. For each structural candidate surviving Phase 3:
   - Compute its molecular formula in Python using RDKit: `from rdkit.Chem import Descriptors; Descriptors.MolecularFormula(mol)` and compare against the locked formula from Phase 1.
   - **Reject any candidate whose formula does not match the locked formula exactly.**
   - Verify the DoU of the candidate equals the DoU from Phase 1.
   - Verify it satisfies every row of the constraint table from Phase 2.
2. **NMR signal count verification** (if the problem states a specific number of NMR signal types):
   - For each candidate, enumerate all chemically distinct proton (or carbon) environments by symmetry. Two protons are equivalent only if they are related by a symmetry operation of the molecule (mirror plane, rotation axis, etc.).
   - Count distinct environments — this is the expected number of NMR signals.
   - **Reject any candidate whose NMR signal count does not match the stated number.**
   - Common trap: a molecule with no symmetry has as many signal types as it has distinct CH groups; a symmetric molecule reduces this count. Do NOT assume a candidate is symmetric without explicitly checking.
   - Do NOT accept a candidate by saying "approximately matches" — the NMR count is an exact integer constraint.
3. **Numerical data consistency** (equivalent weight, molar mass, neutralization volume):
   - Compute the theoretical equivalent weight or molar mass for each candidate and compare to the code-computed value from Phase 1.
   - A discrepancy > 2% is a real contradiction, not a "likely typo." Do NOT dismiss numerical mismatches as transcription errors unless there is an independent reason (e.g., the problem explicitly notes an error).
   - If the discrepancy is small but nonzero, enumerate alternative interpretations (different acid valency, different molecular formula multiplier) before concluding it is a typo.
4. Run: `from rdkit import Chem; mol = Chem.MolFromSmiles(smiles)` to confirm the SMILES is valid.
5. If multiple candidates survive all checks, apply the most discriminating remaining constraint (often the monobromination count or the ozonolysis symmetry argument) to eliminate all but one.

### Phase 5 — Output
1. Output the structural formula (as SMILES and/or IUPAC name).
2. State explicitly which molecular formula it has and confirm it matches Phase 1.
3. For confirmation, output the DoU and show each ring/double bond accounted for.

## Subtype Playbook
- **Symmetric compound with single ozonolysis product**: the compound has a C₂ axis or is symmetric enough that both ozonolysis cuts yield the same fragment. Reconstruct by doubling the fragment.
- **Enol/keto-enol tautomer**: FeCl₃ red + heavy metal complexation + acidic OH + Tollens positive all together → beta-keto aldehyde or beta-diketone with enol form dominant. The OH in IR is the enol OH.
- **Cyclopentadienyl or cyclic conjugated systems**: DoU ≥ 4 for C₁₀H₁₂O suggests two rings + one double bond, or one ring + multiple double bonds. Check if ozonolysis cuts the exocyclic double bond.
- **Symmetry analysis of HBr products**: for a symmetric chain, enumerate positions from the center; positions equidistant from center are equivalent. Count unique positions and whether the carbon becomes a stereocenter upon bromination.

## Output Rules
- Structural formula: SMILES preferred; structural line formula acceptable.
- Molecular formula: CₙHₘOₚ with confirmed match to combustion analysis result.
- DoU: integer, with breakdown.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — task signatures and failure modes for this skill.
