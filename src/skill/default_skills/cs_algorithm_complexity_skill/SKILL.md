---
name: cs_algorithm_complexity_skill
description: Produce exact complexity bounds, query-model answers, code-simulation outputs, or cipher plaintexts for discrete CS problems that require formal modeling rather than intuition.
type: sop
version: 1.1.0
require_grad: true
---

# CS Algorithm & Complexity Skill

## Goal
For algorithmic / complexity / query-model / code-simulation / cipher problems, emit a rigorous Θ/Ω/O bound, a precise integer, or an exact string.

## When to Activate

**Algorithm Complexity & Query Models**
- The problem concerns Θ/Ω/O complexity, query lower bounds, SQ learning, competitive analysis, or formal semantics.
- The problem fixes non-standard parameters ("exactly n trades, budget M") so that canonical bounds do not directly apply.

**Code Simulation / Gap-Fill**
- The problem provides Python (or pseudocode) with a missing expression and asks for the output or the correct fill.

**Cipher / Cryptography**
- The problem provides a cipher text, a key, and asks for the plaintext.

## Common Failure Modes (Avoid These)

*Complexity*
- Borrowing the upper bound of a canonical model and presenting it as a tight bound, ignoring the problem's specific constraints.

*Code Simulation*
- Skipping a line-by-line execution trace and guessing the output instead of running through the code step by step.

*Cipher*
- Assuming Caesar cipher without first reconstructing the substitution table from the stated key.

## SOP — 4 Phases (phase count tailored to this skill's natural workflow)

### Phase 1 — Formal Model & Parameter Ledger
1. Write out the operation set, cost model, success criterion, allowed randomness, and query budget.
2. Record every named parameter (n, M, d, k, L, …) in a ledger; the final answer must either use each parameter or give a rigorous argument why it is absent.
3. Decide the answer shape: Θ / Ω / O / exact integer / string.

### Phase 2 — Known Bounds & Subtype Routing
1. Recall known bounds: SQ-learning LB (FKV / DKS), comparison-sort LB, competitive ratio classics.
2. Route by subtype: complexity vs DP vs cipher vs code-fill vs formal-semantics encoding.
3. Never assume Caesar for cipher problems — first identify the cipher type (substitution / multi-step / Vigenere).

### Phase 3 — Construction + Adversary  /  Simulation
1. For a tight bound, provide an algorithmic construction for the upper bound and an adversary / information-theoretic / counting argument for the lower bound.
2. For DP state-space width problems: compute **each constraint bound independently** (e.g. backward-feasible span = 15n+1, capital cap = M+12n+1), take their min to get W, then do an **explicit case split** (e.g. M≥3n → W=15n+1; M<3n → W=M+12n+1). The final Θ bound is Θ(W·n).
3. For code gap-fill / simulation problems, run the problem's code line by line via Python — do not mentally simulate.
4. For cipher problems, reconstruct the letter table from the stated key first and decode letter by letter.

### Phase 4 — Sanity & Format
1. Plug extreme parameters (M→0, M→∞) and cross-check against brute force.
2. For ω(1)-style notation, confirm the exponent is a function of d and not a constant.
3. Emit Θ / Ω / O exactly as the problem's notation dictates; preserve case for strings; sort dictionary answers by key.

## Subtype Playbook
- **SQ learning lower bounds**: for ReLU networks, consult FKV / DKS lower bounds; note ω(1) is not a constant.
- **Query sorting / bitstring**: information-theoretic lower bound log(N!) combined with prefix-comparison structure.
- **Competitive programming + DP**: write the transition first; for state-space width problems, compute each constraint bound independently → take min → case-split on the crossover parameter (e.g. M≥3n vs M<3n) → final bound is Θ(min{bound_1, bound_2}·n).
- **Linear Logic / Minsky machine encoding**: translate counter state, increment / decrement, zero-check each as a separate LL formula.
- **Cipher + cultural reference**: first build the letter table from the stated key, then resolve the reference.
- **Python code gap-fill**: run the actual interpreter line by line; never simulate in your head.

## Output Rules
- Complexity: Θ(…) / Ω(…) / O(…) strictly in the problem's notation.
- Strings / integers: exact case, no leading or trailing whitespace.
- Dictionary-style answers: [A: 4, B: 9, …] with keys in alphabetical order.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
