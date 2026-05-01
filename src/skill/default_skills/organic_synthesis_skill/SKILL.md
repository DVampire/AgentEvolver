---
name: organic_synthesis_skill
description: Solve organic synthesis problems requiring exact product structure, IUPAC name, or stereochemistry (R/S, E/Z) for named reactions (Wittig, aza-Claisen, Nef, ring-closing metathesis, etc.). Mandatory RDKit verification step for any stereochemical or IUPAC naming claim.
type: sop
version: 1.0.0
require_grad: true
---

# Organic Synthesis Skill

## Goal
For problems that ask for the product of a multi-step or named organic reaction, produce the exact IUPAC name and/or SMILES with correct stereochemistry, validated programmatically.

## When to Activate
- The problem names a specific reaction (Wittig, aza-Claisen, Nef, Grubbs metathesis, Hayashi–Jørgensen, Horner–Wadsworth–Emmons, etc.) and asks for the product name or structure.
- The problem requires assigning absolute/relative configuration (R/S, E/Z, dr, ee) to a product.
- The problem provides a multi-step synthesis sequence and asks which atoms from a starting material survive into the product.
- The problem gives an IUPAC reagent name and requires interpreting it as a SMILES/structure before reasoning about reactivity.

## Common Failure Modes (Avoid These)
- **Parsing reagent names from text alone**: IUPAC names with locants and stereo prefixes are routinely misread. Always convert to SMILES via RDKit or py2opsin before reasoning.
- **Textual R/S assignment**: CIP priority depends on the full substitution graph of the product. After the bond-forming step, priorities change. Never assign R/S by verbal argument alone — always call `rdkit.Chem.AssignStereochemistry` on the product SMILES.
- **E/Z by ylide-type heuristic only**: the non-stabilized → Z rule is a tendency, not a law. Verify with the actual transition-state geometry or literature; do not override a code result with a textual argument.
- **Forgetting to re-check CIP after naming**: after generating a product SMILES, re-extract the IUPAC name with RDKit or iupac_name tools and compare; a mismatch means the SMILES or the name is wrong.
- **Treating synonyms as equivalent without checking**: "methylenecyclopentyl" vs "methylidenecyclopentyl" differ in IUPAC version; always output the IUPAC 2013 preferred name.

## SOP — 5 Phases

### Phase 1 — Structure Parsing
1. For every named reactant/reagent in the problem, convert the IUPAC name → SMILES using a tool call (py2opsin or `Chem.MolFromSmiles(Chem.MolToSmiles(...))`). Do NOT proceed to Phase 2 until every reactant has a confirmed SMILES.
2. Draw the reaction schematically: label the reacting bond(s), the leaving group, and the new bond(s) formed.
3. Identify the reaction type from the reagent/condition fingerprint:
   - `Ph₃P=CR₂` + aldehyde → Wittig (check ylide stabilization: conjugated = stabilized → E; isolated sp3 α-C = non-stabilized → Z tendency)
   - LiHMDS / non-polar solvent / low T then heat → aza-Claisen (Z-enolate → chair TS)
   - `[Ru]=CHPh` (Grubbs) → ring-closing metathesis
   - Ozonolysis / Zn / AcOH → reductive cleavage → aldehyde/ketone
   - NaOMe or KOtBu + NO₂ compound + hν + O₂ / photosensitizer → Nef-type photo-oxidation

### Phase 2 — Mechanism & Bond Tracking
1. Write the mechanism step-by-step as a SMARTS transformation or atom-mapped SMILES.
2. For pericyclic reactions (Claisen, Cope, [2+2], [4+2]): draw the 6- or 4-membered TS explicitly with atom numbering. Label which bond breaks (σ or π) and which forms.
3. For atom-tracing questions ("how many carbons from compound X end up in product Y"): assign explicit atom-map numbers in SMILES (`[C:1]`, `[C:2]`, …) for every atom in the starting material fragment, propagate through the mechanism, and count surviving mapped atoms in the product.

### Phase 3 — Product SMILES Construction
1. Build the product SMILES from the mechanism output. Include all stereo bonds (`/`, `\`, `@`, `@@`) as determined by the TS geometry.
2. For aza-Claisen and related [3,3] shifts:
   - Confirm Z-enolate geometry from LiHMDS + non-polar solvent (Z favored >95:5 for most amide substrates).
   - Place all substituents in the chair TS; use pseudo-axial/equatorial logic to assign the new stereocenters.
   - After assigning the TS geometry, build the product SMILES with explicit `@`/`@@` at each new stereocenter.
3. Run: `from rdkit import Chem; mol = Chem.MolFromSmiles(smiles); Chem.AssignStereochemistry(mol, cleanIt=True, force=True)` and print the CIP codes for every stereocenter. This is **mandatory**; a Phase 3 result without a tool-call CIP output is incomplete.

### Phase 4 — IUPAC Name Generation & Verification
1. Generate the IUPAC name from the product SMILES using a tool call (e.g. `rdkit.Chem.Draw.MolToMolBlock` + external IUPAC namer, or py2opsin in reverse). If no tool is available, write the name manually and then parse it back with py2opsin to verify it round-trips to the same SMILES (canonical form must match).
2. Verify stereodescriptors in the name match the CIP codes from Phase 3.
3. If the problem provides an answer option or ground-truth candidate: parse that candidate with py2opsin, compare canonical SMILES, and state whether they are identical.

### Phase 5 — Final Answer
1. Output the IUPAC name as a single string. No markdown, no explanation.
2. If the problem asks for SMILES instead of or in addition to the name, output the canonical SMILES from `Chem.MolToSmiles(mol)`.
3. For atom-counting questions, output an integer.

## Subtype Playbook
- **Wittig**: parse ylide IUPAC name → check α-carbon substitution → classify stabilized/non-stabilized → predict E/Z tendency → build product SMILES → verify with RDKit CIP.
- **Aza-Claisen**: Z-enolate assumption → chair TS drawing → assign new stereocenters from TS → build SMILES with `@`/`@@` → RDKit CIP check → IUPAC name.
- **Atom tracing in multi-step synthesis**: atom-map every relevant atom in the starting material → propagate through each step → count survivors in product.
- **Ring-closing metathesis**: identify the two terminal alkene partners → form the ring → output cyclic alkene SMILES (geometry usually Z for small rings).

## Python Tool Usage Notes
- Always `pip install rdkit py2opsin` if not present before use.
- For CIP assignment: `from rdkit.Chem import AllChem; AllChem.AssignStereochemistry(mol, cleanIt=True, force=True); [print(a.GetIdx(), a.GetPropsAsDict().get('_CIPCode','')) for a in mol.GetAtoms()]`
- py2opsin converts IUPAC → SMILES: `import subprocess; result = subprocess.run(['java','-jar','opsin.jar', name], capture_output=True, text=True); smiles = result.stdout.strip()`
- Avoid single-quoted strings containing apostrophes; use double-quoted strings.

## Output Rules
- Product name: IUPAC preferred name (IUPAC 2013 recommendations), no trailing punctuation.
- Stereochemistry: R/S and E/Z must match the RDKit CIP output, not the textual argument.
- Atom count: integer only.
- **Canonical SMILES normalization**: before reporting the final answer, always call
  `Chem.MolToSmiles(Chem.MolFromSmiles(smiles))` to obtain the canonical SMILES. Two
  SMILES strings that look different may represent the same molecule — always compare
  canonical forms, not raw input strings.
- **IUPAC name round-trip check**: after generating the IUPAC name, parse it back with
  py2opsin → canonical SMILES and compare against the product's canonical SMILES. If
  they differ, the name is wrong — fix it before reporting.
- **Symmetry group / point group**: if the question asks "what is the symmetry group" or
  "what is the point group", the answer is a single group symbol (e.g. `C₂ᵥ`, `D₃ₕ`,
  `Td`). Never output a list of group names or a conjunction. If the compound has more
  than one possible assignment depending on idealisation, pick the assignment that
  matches the structure as drawn (all bond lengths and angles equal for regular
  geometries).

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — task signatures and failure modes for this skill.
