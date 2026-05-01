---
name: protein_engineering_skill
description: Answer protein mutagenesis and engineering design questions where the correct substitution must preserve structural/disorder context (IDR, linker, domain spacing) in addition to eliminating a specific biochemical property (charge, phosphorylation, interaction surface).
type: sop
version: 1.1.0
require_grad: true
---

# Protein Engineering & Mutagenesis Skill

## Goal
Design the minimal, context-aware substitution that eliminates a targeted biochemical property while preserving the structural role of the mutated segment (disorder, flexibility, spacing, or folded domain integrity).

## When to Activate
- The problem asks which amino acid substitution(s) best abolish a property (charge, phosphorylation, binding, disulfide) in a specific protein region.
- The question specifies or implies a structural context: IDR / intrinsically disordered region, linker, prodomain, ordered domain, or motif.
- The answer requires balancing "remove the functional property" with "maintain the structural character of the segment."
- Keywords: IDR, disordered region, prodomain, flexible linker, acid patch, phosphorylation site, charge neutralization, site-directed mutagenesis.

## Common Failure Modes (Avoid These)
- **Jumping to conservative swaps before checking region type**: treating the problem as a standard point-mutation question (E/D → Q/N, S → A) without first establishing whether the segment is an IDR or linker. In an IDR, individual conservative swaps are the wrong tool even if they neutralize charge.
- **Treating "all function as a unit" as flavour text**: this phrase is a direct signal that the patch must be replaced as a whole, not residue by residue.
- **Phospho-site swap only**: S/T → A blocks phosphorylation but leaves the surrounding acidic residues intact; if those residues are part of the same functional patch, they must also be addressed.
- **Poly-Ala in IDR contexts**: Ala has moderate β-propensity and can nucleate structure in long runs, partially collapsing the disordered region the question is trying to preserve.

## SOP — 4 Phases

### Phase 1 — Establish Region Type First (Gate Decision)
1. Read the problem for explicit region labels: IDR, intrinsically disordered, prodomain, flexible linker, ordered domain, structured domain.
2. Look for collective-function cues: "function as a unit", "all residues contribute", "patch", "spacing must be maintained". If present alongside an IDR label, the entire patch must be replaced together — do not proceed residue by residue.
3. Record the targeted biochemical property to eliminate (negative charge, phosphorylation, hydrophobic surface, disulfide, PPI contact).
4. Record the exact residue window and identities.

### Phase 2 — Choose Strategy Based on Region Type
**If IDR or flexible linker** (with or without "function as a unit" cue):
→ Replace the entire patch with a Gly/Ser-rich sequence of equal length. This simultaneously strips charge, removes PTM context, and preserves backbone flexibility and segment spacing. Do not use conservative isosteric swaps — they are the wrong strategy here regardless of how well they neutralize charge.

**If ordered domain**:
→ Use conservative isosteric swaps residue by residue (E→Q, D→N, S→A, C→A). Extend only to residues with positive evidence of contributing to the functional property.

**If surface-exposed motif / PPI interface**:
→ Ala-scan strictly the identified contact residues; leave non-contact residues wild-type.

**Tie-breaking rule**: when the region type is ambiguous, default to the IDR/linker strategy if the problem mentions prodomain, spacing, or flexibility — these are stronger signals than the absence of an explicit "IDR" label.

### Phase 3 — Validate the Candidate Design
1. **Charge check**: count net charge of original vs mutant segment; the target charge change should match the problem's intent.
2. **PTM site check**: confirm phosphorylatable S/T/Y residues in the patch are removed or rendered non-phosphorylatable.
3. **Disorder preservation**: Gly and Ser have high disorder propensity; Ala has moderate β-propensity and can nucleate structure in long runs — avoid poly-Ala in IDR contexts.
4. **Spacing / length check**: substitution should preserve the number of residues (no insertions/deletions unless explicitly asked).
5. **Minimal perturbation**: do not mutate residues outside the identified functional patch.

### Phase 4 — Self-Audit & Output
1. Re-read the question for any constraint you may have deprioritized (e.g. "without deleting the prodomain", "retains overall structure").
2. If multiple valid strategies exist, rank them: IDR-context problems rank Gly-Ser linker replacement above conservative charge-neutralization.
3. State the final substitution in one-letter code and give a one-sentence rationale covering both what was removed and what was preserved.

## Subtype Playbook
- **Acid patch in IDR (E/D cluster with or without adjacent S/T)**: replace entire patch with a Gly/Ser-rich linker of equal length; eliminates negative charge and phosphorylation context while preserving IDR flexibility. The canonical linker pattern for a 4-residue patch is Gly-Ser-Gly-Gly: Gly at positions 1, 3, 4 for maximum flexibility; Ser at position 2 for hydrophilicity without charge or rigidity. Do NOT use poly-Gly — a single Ser is required to maintain hydrophilic character. Poly-Ala is inferior (collapses disorder); individual Q/N swaps are inferior (retain partial rigidity).
- **Linker design when specific sequence context is given (e.g. "prodomain" or "mature protein requires a precise linker")**: when the problem specifies a named linker type or functional domain context beyond generic charge neutralization (e.g., a designed processing linker, a furin cleavage site spacer, or a domain-boundary linker), read the problem's own description of what the linker must accomplish. Do NOT apply the generic Gly/Ser IDR replacement if the problem implies a specific linker sequence or constraint (e.g., a particular amino acid composition required for protease recognition). In these cases, derive the linker from the stated functional requirement first.
- **Phosphorylation site in ordered domain**: S/T → A blocks phosphorylation; extend to flanking acidic residues only if they form a composite docking surface with positive evidence.
- **Disulfide bond abolition**: C → A for buried cysteines; C → S for surface-exposed cysteines where retaining a hydroxyl is more conservative.
- **Hydrophobic patch**: confirm burial depth first; buried → Ala; surface-exposed → Ser may be preferred to reduce aggregation risk.
- **PPI interaction surface**: Ala-scan strictly the identified contact residues; non-contact residues should remain wild-type.

## Output Rules
- Commit to a single best answer — do not use "e.g.", "or", "such as", or present multiple options. If the reasoning leads to multiple valid candidates, pick the one that best satisfies all constraints and state only that one.
- Express the final substitution using full amino acid names in order (e.g. "[Name]-[Name]-... linker"), not as one-letter code abbreviations or sequence shorthand, unless the problem explicitly asks for one-letter code.
- Follow the answer format the problem uses (sequence string, list of point mutations, or multiple-choice letter).
- Include a brief rationale (≤2 sentences) covering the functional property removed and the structural property preserved.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
