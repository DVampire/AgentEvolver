---
name: materials_property_prediction_skill
description: Resolve materials-science, nanotechnology, and chemistry property questions that combine experimental data interpretation (XRD, optical propagation, descriptor definitions) with physical/chemical hard constraints (symmetry, thermal stability).
type: sop
version: 1.0.0
require_grad: true
---

# Materials & Chemistry Property Prediction Skill

## Goal
Combine experimental data with physical/chemical hard constraints to produce a phase assignment, a yes/no verdict, or an exact descriptor value.

## When to Activate
- The problem contains XRD data / an optical-propagation description / a molecular descriptor (Böttcher, Geary) / a liquid-crystal design goal.
- The answer is phase + space group + lattice constants, a Yes/No verdict, or an exact decimal.
- Symmetry-driven properties (non-linear optics, SPDC, spontaneous polarization) need to be decided.

## Common Failure Modes (Avoid These)
- For spectra, using only the strongest peak instead of the full fingerprint pattern.
- Ignoring hard symmetry constraints (centrosymmetric material ⇒ χ^{(2)} forbidden).
- For descriptor computations (Böttcher / Geary), estimating from memory instead of evaluating the definition term by term.
- Stopping at phase identification without providing the full tuple (phase + space group + all lattice constants including β for monoclinic).

## SOP — 4 Phases (phase count tailored to this skill's natural workflow)

### Phase 1 — Digest Experimental / Design Input
1. Reduce the data into a standardized parameter table (2θ peaks, beam mode, SMILES / bond graph, phase-transition window).
2. List the hard physical / chemical constraints of the system (symmetry / bond valence / coordination / thermal window / solvent compatibility).
3. Identify the answer type (phase assignment / Yes-No / exact number).

### Phase 2 — Hard-Constraint Filtering
1. XRD: fingerprint via peak positions + intensity pattern, not the strongest peak alone.
2. Non-linear optics / SPDC: centrosymmetric ⇒ χ^{(2)} forbidden; non-centrosymmetric ⇒ χ^{(2)} allowed. Critically: the symmetry verdict depends on the **material form** (bulk vs free-standing nanosheet vs thin film), not just the bulk crystal class. Bulk boron = centrosymmetric; free-standing boron nanosheet = breaks inversion ⇒ SPDC allowed.
3. Liquid-crystal design: apply constraints sequentially (core geometry → tail length → transition temperature window) and eliminate options that fail any one constraint. Score remaining options across all criteria simultaneously.
4. For multiple-choice materials problems: one option must satisfy ALL stated constraints while others fail at least one — do not pick based on partial matches.

### Phase 3 — Quantify Descriptor / Identify Phase
1. XRD: provide phase + space group + a / b / c / β as a full tuple.
2. Böttcher molecular complexity: evaluate C = Σ d_i × e_i × s_i × … atom by atom from the definition.
3. Geary autocorrelation: pick the lag i_max, then take the weighted sum (Sanderson electronegativity).

### Phase 4 — Literature-Parity Cross-Check & Output
1. Compare against published values for analogous systems; if the deviation is large, return to Phase 3.
2. For Yes/No questions, state the physical / chemical reason explicitly before emitting the verdict.
3. Follow the problem's precision and unit convention ("a = 4.7 Å", "55.51").

## Subtype Playbook
- **XRD phase identification**: fingerprint by 2θ peak positions + intensity ratios; monoclinic reports β, cubic reports only a. Use Python + Bragg's law (d = λ/2sinθ) to convert 2θ peaks to d-spacings, then match against JCPDS cards.
- **SPDC / non-linear optics**: determine material form first (bulk vs nanosheet vs film); bulk centrosymmetric ⇒ No; free-standing nanosheet breaks inversion ⇒ Yes. Do not assume the bulk crystal class applies to low-dimensional forms.
- **Liquid-crystal multiple-choice**: apply constraints as hard filters sequentially (core → tail → transition window) and select the unique option satisfying all; options satisfying only subset constraints are wrong.
- **Laguerre-Gaussian beams in random media**: distinguish polarization dephasing from mode decoherence.
- **Böttcher complexity**: sum atom by atom strictly from the original definition.
- **Geary autocorrelation (MATS / GATS)**: pick lag, then apply the weighted sum.

## Output Rules
- XRD answers follow the example format ("Monoclinic CuO C2/c sp.gr. a = 4.7 Å …").
- Yes/No answers use the exact wording the problem asks for.
- Descriptor precision follows the problem's example.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
