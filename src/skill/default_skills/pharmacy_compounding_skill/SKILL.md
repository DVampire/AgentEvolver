---
name: pharmacy_compounding_skill
description: Answer questions about pharmaceutical compounding standards, beyond-use dating (BUD), sterile preparation requirements, and container/storage regulations based on USP <797>, USP <800>, and related compounding guidelines.
type: sop
version: 1.0.0
require_grad: true
---

# Pharmacy Compounding Skill

## Goal
Identify the correct BUD, storage condition, preparation requirement, or compounding classification based on the container type, preparation category, environmental conditions, and applicable USP chapter.

## When to Activate
- The problem asks about beyond-use date (BUD) for a compounded or opened preparation.
- The question involves sterile vs non-sterile compounding classification.
- The problem references container types (single-dose, multi-dose, ampule, vial) and asks about time limits after opening or puncture.
- Keywords: BUD, beyond-use date, USP 797, USP 800, sterile compounding, ampule, single-dose, multi-dose, ISO 5, clean room, puncture, beyond use.

## Common Failure Modes (Avoid These)
- **Confusing expiration date with BUD**: expiration date is set by the manufacturer; BUD is set after opening, puncturing, or compounding.
- **Confusing ampule BUD with single-dose vial BUD**: Under USP <797>, a single-dose *vial* punctured in an ISO 5 environment has a 12-hour BUD. A single-dose *ampule* is different — once opened it has a very short BUD (1 hour) because it cannot be resealed and lacks a rubber septum to maintain sterility. Do NOT apply the 12-hour vial rule to an ampule.
- **Ignoring environmental conditions**: BUD differs based on whether the preparation is handled in a sterile environment (ISO 5 or better) vs a non-sterile environment — always check the stated environment before applying a rule.
- **Confusing single-dose and multi-dose container rules**: single-dose containers have stricter BUD limits than multi-dose containers after opening.
- **Applying the wrong USP chapter**: USP <797> governs sterile compounding; USP <800> governs hazardous drugs. Use the correct chapter for the scenario.

## SOP — 3 Phases

### Phase 1 — Identify the Preparation Type and Context
1. Determine container type: single-dose (ampule, single-use vial) vs multi-dose vial.
2. Determine the environment: sterile (ISO 5 / laminar airflow / clean room) vs non-sterile (general pharmacy or patient care area).
3. Determine the action: puncture, opening, compounding, or admixture preparation.
4. Identify which USP chapter applies to the scenario.

### Phase 2 — Apply the Correct BUD Rule
1. Look up the applicable BUD based on container type × environment combination.
2. Key rules to apply:
   - Single-dose containers (ampules, single-use vials): apply the single-dose BUD rule for the stated environment.
   - Multi-dose vials: apply the multi-dose BUD rule; note whether the manufacturer has specified a different in-use period.
   - Compounded sterile preparations (CSPs): classify by risk level or category, then apply the corresponding BUD.
3. If the environment is not stated, default to the more conservative (shorter) BUD.

### Phase 3 — State the Answer
1. Give the BUD as a specific time value with units.
2. Cite the applicable rule (container type + environment + USP chapter).
3. Note any conditions that would change the BUD (e.g. refrigeration, different environment).

## Subtype Playbook
- **Ampule (glass, single-dose, no rubber septum) in sterile environment**: BUD = **1 hour** from the time of opening. Ampules cannot be resealed; once the glass neck is broken, sterility cannot be maintained beyond 1 hour even in an ISO 5 environment. Do NOT apply the 12-hour rule — that applies to single-dose vials, not ampules.
- **Single-dose vial (SDV) in ISO 5 or better**: BUD = 12 hours from first puncture in an ISO 5 environment; 1 hour in a non-ISO 5 environment.
- **Single-dose vial in non-sterile environment**: BUD = 1 hour from first puncture.
- **Multi-dose vial after first puncture**: BUD = 28 days after initial puncture, unless manufacturer specifies shorter; discard if contamination is suspected.
- **Compounded sterile preparation (CSP)**: classify by category (immediate use, Category 1, Category 2), then apply corresponding BUD based on storage condition (room temperature, refrigerated, frozen).

## Output Rules
- State the BUD as a single time value with units (e.g. "[N] hours" or "[N] days").
- Cite the container type, environment, and applicable USP chapter.
- If multiple conditions apply, state the most conservative BUD first.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
