---
name: image_grounded_expert_reasoning_skill
description: Solve problems whose correctness depends on faithfully extracting structured information from attached images — plots, multi-panel figures, gels, Google Trends, photographs, diagrams — and then matching extracted features against a domain hypothesis set.
type: sop
version: 1.0.0
require_grad: true
---

# Image-Grounded Expert Reasoning Skill

## Goal
After fully structuring the visual input, map image features across domains onto the candidate set and output precise identifier strings / scientific names / parameter tuples.

## When to Activate
- has_image=True and the problem text directly references the image ("the attached image", "plot 3").
- Answer form is a panel-index string, a scientific name, coordinate values, or a multi-parameter tuple.
- The problem provides a candidate parameter list or candidate-object list and asks you to match it against the image.

## Common Failure Modes (Avoid These)
- Reading only the primary visual while ignoring legend, color mapping, sub-panel indices, and text labels.
- Turning a multi-panel matching task into a guess instead of quantitatively comparing each panel's features against the candidate table.
- Ignoring auxiliary clues provided in the problem text (genome length, units, time window) and relying on the figure caption alone.

## SOP — 4 Phases (phase count tailored to this skill's natural workflow)

### Phase 1 — Exhaustive Image Inventory
1. Build one record per sub-panel: {index, axis ranges, units, legend / color mapping, text labels, recognizable feature points}.
2. Do not skip the legend or small-print caption — it is often the decisive clue.
3. In multi-panel tasks, always follow the panel ordering given by the problem.

### Phase 2 — Feature ↔ Hypothesis Matching
1. Arrange the candidate parameters / scientific names / events as comparable feature vectors.
2. For string-matching answers ("sort the panels as a character string"), nail down which character position maps to which panel.
3. For identification tasks, first narrow to the broad class (phylum / family / genre / era), then refine down to species / specific work.

### Phase 3 — Unused-Label Self-Audit
1. Ask: is there any prominent label in the image I have NOT used? If so it is likely the key clue to the answer.
2. For every panel, require two independent visual features to agree before concluding.
3. If two candidates both match a single panel, refine the feature; if they are still indistinguishable, return to Phase 1 and re-read the image.

### Phase 4 — Output Formatting
1. For index-string answers, produce one character per panel in the order specified by the problem; no separator unless requested.
2. Scientific names follow Genus species: genus capitalized, epithet lower-case.
3. Numeric answers carry units only if the problem specifies them.

## Subtype Playbook
- **Multi-panel physical simulations (wave / heat / fluid)**: use steady-state value, symmetry, and first zero-crossing as robust features.
- **RT-PCR / Western-blot gels**: count bands, align molecular-weight ladder, match each lane to its sample label.
- **Biological species identification**: combine body features (wing spots, abdominal segments, eyespots) with geographic / plant cues in the image.
- **Historical / art images**: conjoin costume, composition, symbolism, and background text as a multi-constraint match.
- **Google Trends**: inspect peak time and relative height and map the event sequence to candidate events.
- **Geometric identification (Goldberg polyhedra etc.)**: count faces, vertices, edges first, then apply the classification formula.

## Output Rules
- String answers follow the exact case and separator shown in the problem.
- Scientific names follow binomial nomenclature; numerical precision mirrors the problem's example.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
