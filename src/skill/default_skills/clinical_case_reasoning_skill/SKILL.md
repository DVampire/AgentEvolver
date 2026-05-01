---
name: clinical_case_reasoning_skill
description: Reason through complex clinical case scenarios to identify the primary diagnosis (including rare syndromes like Felty's, overlap syndromes, paraneoplastic) by anchoring on the main disease timeline and not being misled by secondary complications or travel history; also analyze laboratory QC failures by identifying whether the control organism, culture passage, or reagent was the root cause.
type: sop
version: 1.0.0
require_grad: true
---

# Clinical Case Reasoning Skill

## Goal
Produce a precise diagnosis or root-cause identification from a complex clinical narrative by following a structured reasoning framework, avoiding common traps such as being misled by late-stage complications or red-herring exposures.

## SOP
1. Identify which domain the question belongs to using the activation keywords below.
2. Follow that domain's SOP.
3. Apply the Output Rules.

---

## Domain 1: Complex Clinical Case Differential Diagnosis

### When to Activate
- The problem presents a multi-paragraph clinical vignette with a timeline of evolving symptoms.
- The question asks for the primary diagnosis, underlying disease, or syndrome.
- The case includes late-stage complications, travel history, or opportunistic infections that could distract from the primary disease.
- Key vocabulary: syndrome, underlying disease, primary diagnosis, what disease did the patient have, what condition explains.

### Common Mistakes
- **Being misled by the terminal event**: the cause of death or final acute episode is often a complication of the primary disease, not the primary disease itself. Identify the underlying condition that set the stage for everything else.
- **Anchoring on travel history or environmental exposure**: geographic exposures (Africa, Asia, construction sites) introduce infectious disease anchors that can override careful reading of the chronic disease trajectory. Evaluate these as possible secondary infections superimposed on a primary condition.
- **Ignoring treatment response clues**: response to steroids/NSAIDs points toward autoimmune or inflammatory disease; failure of antibiotics points away from a straightforward infection as the primary diagnosis.
- **Missing the triad or classic combination**: many syndromes are defined by a specific combination (e.g., arthritis + splenomegaly + leukopenia; rash + renal + hematologic). Explicitly check whether the full syndrome definition is met before settling on a diagnosis.

### SOP
1. **Build a chronological timeline** of all symptoms, separating early/chronic findings from late/acute ones.
2. **Identify the anchor diagnosis** from the earliest and most persistent symptoms — these define the primary disease. Do not let late-stage events override the anchor.
3. **Classify each symptom** as: (a) primary disease manifestation, (b) complication of primary disease, or (c) unrelated/secondary event.
4. **Check for named syndrome criteria**: if the anchor diagnosis is an autoimmune or inflammatory disease, explicitly check whether a named syndrome (e.g., Felty's, Sjögren's overlap, CREST) explains the full picture better than the base diagnosis alone.
5. **Evaluate secondary events in context**: infections, organ failure, or sepsis occurring late in the timeline are usually complications of the primary disease (especially if the patient is immunosuppressed by treatment or by the disease itself).
6. **State the primary diagnosis** — the condition that best explains the full timeline from earliest symptom to final outcome.

---

## Domain 2: Laboratory QC Failure Root-Cause Analysis

### When to Activate
- The problem describes a microbiological experiment where a QC check gave unexpected results (false negative, false positive, or no growth when growth was expected).
- The question asks why the lab made an error in interpreting the QC result.
- Key vocabulary: QC, quality control, ATCC, passage, subculture, control organism, media batch, autoclave, disinfectant, viable, non-viable.

### Common Mistakes
- **Blaming the reagent or media first**: autoclaving degrades heat-labile additives (e.g., chloramphenicol), but this would cause false positives (bacteria grow), not false negatives (no growth). Match the failure mode to the observation direction before assigning blame.
- **Ignoring passage number and culture age**: ATCC strains repassaged repeatedly from working stocks lose viability or phenotypic stability. A control organism that has been passaged too many times or stored too long may fail to grow — yielding a false-negative QC result that makes a bad batch appear acceptable.
- **Confusing "expected result" direction**: clarify what a correct QC result looks like, then determine whether the observed result was a false positive or false negative, before reasoning about the cause.

### SOP
1. **Identify the QC design**: what organism was used, what was the expected result (growth / no growth), and what was actually observed.
2. **Determine the failure direction**: false positive (growth when none expected) vs false negative (no growth when growth expected).
3. **Evaluate the control organism's viability**:
   - Check passage history: strains passaged beyond recommended limits (typically ≤5 passages from certified stock) may be non-viable or phenotypically altered.
   - Check culture age: old plates or stocks stored improperly lose viability.
   - If the control organism was non-viable, any result produced by that QC check is uninformative — the batch cannot be validated.
4. **Evaluate the media/reagent**:
   - For false positives: check whether heat-labile selective agents (antibiotics) were degraded during autoclaving.
   - For false negatives: check whether the growth-supporting components were compromised (wrong pH, wrong temperature, contamination).
5. **Identify the root cause** that best explains the observed failure direction, prioritizing the control organism's viability before blaming the media.

---

## Output Rules
- Domain 1: state the primary diagnosis as a specific named syndrome or disease; one short phrase, no preamble.
- Domain 2: state the root cause as a single short declarative sentence identifying what failed and why the QC result was unreliable.
