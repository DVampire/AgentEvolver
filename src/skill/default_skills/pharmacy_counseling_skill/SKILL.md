---
name: pharmacy_counseling_skill
description: Identify the most clinically significant counseling recommendation for a patient's medication regimen, including contraindications, drug interactions, and drug-disease conflicts, given patient history and current prescriptions.
type: sop
version: 1.1.0
require_grad: true
---

# Pharmacy Counseling Skill

## Goal
Identify the single most important counseling recommendation or medication selection based on the patient's clinical context, prescription list, and any OTC drugs or supplements mentioned. Also covers drug regimen optimization questions where a specific drug class protocol must be applied.

## When to Activate
- The problem describes a patient with a medication list and asks what counseling a pharmacist should provide.
- The question requires identifying a contraindication, a drug-disease conflict, or a clinically significant drug interaction.
- The question asks to recommend medications for a clinical condition given a patient profile and exclusion list.
- Patient history contains implicit clinical clues (symptoms, recent events, lifestyle) that must be interpreted before evaluating the medications.
- The question asks about the biochemical mechanism of a drug-related adverse reaction (SJS/TEN, hepatotoxicity, etc.).

## Common Failure Modes (Avoid These)
- **Missing the clinical diagnosis in the history**: patient symptoms described in the story are often the key to identifying a contraindication — do not skip the history and jump straight to drug interactions.
- **Focusing on drug-drug interactions when the answer is a drug-disease contraindication**: the highest-priority counseling point is often a contraindication between a medication and a condition implied by the patient's symptoms, not a pharmacokinetic interaction between two drugs.
- **Treating symptom descriptions as irrelevant flavor text**: specific triggers (smell, light, aura), timing, or context clues are diagnostic signals — interpret them before evaluating the drug list.
- **Stopping at the most obvious interaction**: common interactions (e.g. SSRI + aspirin bleeding risk) may be present but not the intended answer if a more serious contraindication exists.
- **Defaulting to a first-line 3-drug regimen when "resistant HTN" is stated**: resistant HTN (BP uncontrolled on 3 drugs) has a specific 4th-agent protocol — spironolactone (mineralocorticoid receptor antagonist) is the evidence-based add-on. Do not simply select a different first-line agent.
- **Identifying the immune mechanism for SJS/TEN when the biochemical mechanism is asked**: The question may specifically ask for the initiating *biochemical* reaction. For lamotrigine + valproate → SJS/TEN: the initiating biochemical event is **inhibition of lamotrigine glucuronidation by valproate**, not HLA activation or arene oxide formation.

## SOP — 3 Phases

### Phase 1 — Diagnose the Clinical Context
1. Read the patient history carefully. Extract:
   - Symptoms and their triggers (e.g. smell-triggered headache → migraine with aura)
   - Recent events or exposures
   - Any OTC medications taken and why
2. Identify the most likely clinical condition implied by the history, even if not explicitly stated.
3. Flag whether this condition has known drug contraindications (e.g. migraine with aura + combined hormonal contraceptives → stroke risk).

### Phase 2 — Evaluate the Medication List Against the Clinical Context
1. For each prescription and OTC drug, check in order:
   - **Drug-disease contraindication**: is this drug contraindicated given the condition identified in Phase 1?
   - **Drug-drug interaction**: does this drug interact significantly with another drug on the list?
   - **Drug-class rule**: are there class-level warnings that apply (e.g. combined oral contraceptives in migraine with aura)?
2. Rank findings by clinical severity. Contraindications (especially stroke, bleeding, or death risk) outrank pharmacokinetic interactions.
3. For **regimen optimization questions**: identify which clinical guideline pathway applies to the specific diagnosis (e.g., resistant HTN requires a specific step-therapy protocol, not just selection from first-line agents).

### Phase 3 — Formulate the Counseling Recommendation
1. State the single most important recommendation clearly.
2. Name the specific drug(s) involved and the specific risk.
3. State the alternative if one exists (e.g. switch combined OCP to progestin-only pill).
4. Do not list every interaction found — focus on the highest-priority finding.

## Subtype Playbook
- **Drug-disease contraindication (highest priority)**: when a patient's symptoms imply a specific diagnosis, check whether any prescribed drug is contraindicated in that condition. State the condition, the contraindicated drug, the risk, and the recommended alternative.
- **Drug-drug interaction involving an OTC product**: patients often self-select OTC drugs without realizing they interact with prescriptions. Always evaluate OTC drugs mentioned in the history against the full prescription list.
- **Drug-class contraindication**: some contraindications apply to an entire drug class (e.g. a hormone class, an antibiotic class) rather than a single agent. Identify the class and apply the rule uniformly.
- **Absorption interaction**: certain drug classes have well-known absorption interactions with food, supplements, or co-administered drugs. Counsel on timing or avoidance as appropriate.
- **Resistant HTN (BP uncontrolled on ≥3 drugs) — 3-drug optimization**: when asked to maximize HTN treatment with exactly 3 drugs, the resistant HTN protocol calls for: (1) a thiazide-like diuretic (chlorthalidone preferred over HCTZ), (2) a mineralocorticoid receptor antagonist — spironolactone is the evidence-based add-on for resistant HTN, (3) a CCB — prefer the non-dihydropyridine diltiazem over amlodipine in this context when verapamil is excluded, as diltiazem provides both rate control and vasodilation for resistant HTN profiles. Do NOT simply pick a standard ACEi/ARB + DHP-CCB + thiazide first-line combination — that is for uncontrolled Stage 1/2 HTN, not resistant HTN.
- **SJS/TEN biochemical mechanism (lamotrigine + valproate)**: When asked for the *biochemical* initiating reaction, the answer is: valproate inhibits UDP-glucuronosyltransferase (UGT), the enzyme responsible for lamotrigine glucuronidation. This inhibition raises lamotrigine plasma levels, triggering the cutaneous hypersensitivity reaction. This is a *metabolic drug-drug interaction*, NOT a hapten/HLA mechanism. Do NOT describe arene oxide formation unless the question specifies carbamazepine or phenytoin as the culprit drug.

## Output Rules
- **The primary answer MUST be a single imperative sentence** stating the highest-priority clinical action only. Format: "[Action verb] [drug/class]." Examples: "Switch to progestin-only pill." "Discontinue metformin." "Add spironolactone."
- Do NOT lead with explanatory sentences, risk descriptions, or "the pharmacist should advise…" preamble. State the action directly.
- Name the specific drug(s) and risk in the same sentence only if it does not exceed one sentence total.
- Secondary findings (e.g. a drug-drug interaction that is lower priority than the main drug-disease contraindication) may follow as a brief second sentence, but they must be clearly subordinate and never reorder the priority.
- If the question asks "what counseling recommendation could the pharmacist make", output the recommendation itself — not a description of what the pharmacist would say.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
