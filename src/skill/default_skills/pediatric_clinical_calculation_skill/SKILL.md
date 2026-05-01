---
name: pediatric_clinical_calculation_skill
description: Calculate pediatric fluid therapy (resuscitation bolus, maintenance by Holliday-Segar, deficit replacement) and BSA-based drug/fluid dosing for scenarios involving multi-phase fluid regimens, burn patients, or dehydration management.
type: sop
version: 1.1.0
require_grad: true
---

# Pediatric Clinical Calculation Skill

## Goal
Compute exact numeric values (volumes in mL or rates in mL/hr or cc/hr) for multi-phase pediatric fluid management regimens, including resuscitation boluses, maintenance fluids, and deficit replacement, accounting for co-administered fluids and clinical modifiers.

## When to Activate
- The problem asks for fluid volumes or rates in pediatric patients using named formulas (Holliday-Segar, Galveston, Parkland).
- Multi-phase regimens are described (e.g., Phase 1 = resuscitation bolus, Phase 2 = maintenance, Phase 3 = deficit replacement).
- The patient has burns, dehydration, or a clinical condition that modifies the base fluid calculation.
- BSA (m²) or body weight (kg) is given and the question asks for a dosing rate in mL/hr or cc/hr.

## Common Failure Modes (Avoid These)
- **Confusing resuscitation fluid with maintenance fluid**: The Galveston formula has two components: maintenance = 2,000 mL/m²/day of total BSA; resuscitation = 5,000 mL/m²/day of burned BSA. When a question asks only for "maintenance fluid needs" in a burn patient and gives BSA, apply ONLY the 2,000 mL/m²/day maintenance component unless the question explicitly asks for the total Galveston volume.
- **Omitting evaporative loss in burn patients**: Burn patients have significantly increased insensible water loss through damaged skin. The complete Galveston maintenance formula includes an evaporative loss component proportional to the burned surface area (TBSA%). When a burn patient's TBSA% and BSA are both given, the full Galveston maintenance = base maintenance + evaporative loss. Do NOT use only the 2,000 mL/m²/day base term in a burn patient — compute the evaporative loss term as well.
- **Forgetting to subtract co-administered fluids**: When a patient receives IV antibiotics, enteral nutrition, or other IV drips, the prescription volume of the maintenance fluid must be reduced by those volumes.
- **Forgetting mechanical ventilation correction**: Mechanically ventilated patients using humidified circuits lose negligible respiratory water. Guidelines recommend reducing Holliday-Segar maintenance by approximately 20% (multiply by 0.8) for ventilated patients.
- **Misreading "10% dehydration"**: A 10% dehydration deficit in a patient of X kg = 0.10 × X kg = 0.10 × X liters = 0.10 × X × 1000 mL. Do NOT subtract the resuscitation bolus from the total deficit unless the question specifically says to do so.
- **Converting mL/day → mL/hr incorrectly**: Divide total daily volume by 24, not 12 or 8.

## SOP — 4 Phases

### Phase 1 — Identify the Formula and Modifiers
1. Identify the formula specified or implied by the problem: Holliday-Segar (weight-based), Galveston (BSA-based), Parkland (burn resuscitation).
2. Note any clinical modifiers: mechanical ventilation, co-administered IV fluids, enteral nutrition, burn area %.
3. Note the required output format: total mL, mL/day, mL/hr, or cc/hr.

### Phase 2 — Compute Base Volume for Each Phase
Apply the relevant formula for each phase described in the problem.

**Holliday-Segar Maintenance (daily):**
- First 10 kg: 100 mL/kg/day
- Next 10 kg: 50 mL/kg/day
- Each kg > 20 kg: 20 mL/kg/day
- Hourly equivalent (4-2-1 rule): 4 mL/kg/hr for first 10 kg, 2 mL/kg/hr for next 10 kg, 1 mL/kg/hr for remaining.

**Galveston Maintenance (burn patients, BSA-based — FULL formula):**
- Base maintenance = 2,000 mL/m² total BSA / day
- Evaporative loss = 35 mL × %TBSA × total BSA (m²) / day
- **Total Galveston maintenance = base maintenance + evaporative loss**
- When both TBSA% and BSA are provided in a burn patient, ALWAYS compute the evaporative loss term and add it to the base. Using only the 2,000 mL/m²/day term underestimates fluid needs.

**Galveston Resuscitation (burn patients, additional):**
- Resuscitation = 5,000 mL/m² burned BSA / day (only for burn resuscitation; do NOT add this to maintenance unless asked for the total Galveston fluid)

**Resuscitation Bolus:**
- Bolus volume = bolus_rate (mL/kg) × weight (kg)

**Deficit Replacement:**
- Deficit = dehydration_percentage × weight_in_kg × 1000 mL (e.g., 10% dehydration in 12 kg patient = 1,200 mL)
- Distribute over the specified replacement period

### Phase 3 — Apply Modifiers
Apply modifiers in this order:
1. **Co-administered fluids**: subtract the daily volume of all other fluid sources (IV antibiotics, enteral nutrition, drips) from the base maintenance volume.
   - After co-fluid subtraction = base maintenance − co-administered fluids
2. **Mechanical ventilation**: mechanically ventilated patients with humidified circuits lose less insensible respiratory water. Subtract ~80 mL/day (approximately 20% of the ~400 mL/day respiratory insensible loss for a pediatric patient). Do NOT multiply the entire maintenance volume by 0.8 — apply it as a fixed 80 mL/day subtraction.
   - Final adjusted maintenance = (base maintenance − co-fluids) − 80 mL/day (vent correction)

### Phase 4 — Convert and Report
1. Convert to the required output unit (mL/hr = mL/day ÷ 24; cc/hr = mL/hr).
2. Round to the precision requested.
3. Report each phase answer separately in the order requested.

## Subtype Playbook
- **Three-phase dehydration regimen (bolus + maintenance + deficit)**: Calculate each phase independently. Apply vent and co-fluid corrections to maintenance only. Do NOT subtract the bolus from the deficit unless explicitly specified.
- **Burn patient (BSA-based maintenance, cc/hr)**: Use full Galveston maintenance formula: (2,000 mL/m²/day × total BSA) + (35 mL × %TBSA × BSA). Convert to cc/hr by dividing by 24. When TBSA% is provided, it IS needed for the evaporative loss term — do not ignore it even when the question asks only for maintenance.
- **Multi-answer format "X,Y,Z"**: Report results in the exact order the question lists the phases, separated by commas, with no units unless specified.

## Output Rules
- Report each numeric answer in the exact format and order requested.
- Show the key calculation steps (formula used, intermediate products) before the final answer.
- If the question asks for a comma-separated list, output that format exactly.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
