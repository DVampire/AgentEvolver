---
name: variational_nonlinear_pde_skill
description: Solve qualitative True/False and Yes/No questions about nonlinear variational PDEs in anisotropic Sobolev spaces — covering Pohozaev identity, mountain pass geometry, fiber maps, and ground state existence for coupled Schrödinger systems.
type: sop
version: 1.0.0
require_grad: true
---

# Variational Nonlinear PDE Skill

## Goal
For problems about nonlinear variational PDEs in functional-analytic settings (Sobolev spaces, Pohozaev manifolds, mountain pass critical points, constrained ground states), produce a formally correct multi-part True/False / Yes/No verdict for each sub-question.

## When to Activate
- Problem defines a Sobolev-space framework: H^{1,s}(ℝ^n), H¹(ℝ^n), or anisotropic analogues, with an energy functional J or coupled system (u, v).
- Problem asks qualitative structural questions: boundedness from below, existence/uniqueness of critical points, mountain pass geometry, ground state existence, Pohozaev identity implications.
- Answer format is multi-part (a)/(b)/(c) True/False; Yes/No — NOT an explicit closed-form computation.
- Key vocabulary: mountain pass, Pohozaev identity, Pohozaev manifold P(a,b), fiber map / scaling fiber, ground state solution, L²-norm constraint, mass constraint.

## Do NOT Activate If
- The question asks for an explicit closed-form solution (integral, formula) → use `differential_equation_integration_skill`.
- The question is about abstract algebraic structures (Lie groups, dessins, K-theory) with no energy functional or Sobolev space.

## Common Failure Modes (Avoid These)
- **Pohozaev identity scope**: P(u,v) = 0 is NOT merely a necessary condition — in the coupled anisotropic system it is equivalent to (u,v) being a critical point of J. Do not answer False when True.
- **Fiber map uniqueness**: For any non-zero (u,v) ∈ H^{1,s}, there exists a UNIQUE t > 0 such that (u_t, v_t) lies on the Pohozaev manifold P. This is a standard result; do not deny it.
- **Mountain pass → ground state**: In L²-norm-constrained settings, when J has mountain pass geometry and a critical point exists at the mountain pass level, that critical point IS a positive ground state (after symmetry arguments). Do not conflate the unconstrained and constrained cases.
- **Uniqueness on P(a,b)**: Uniqueness of the minimizer over P(a,b) requires the exponent range r₁+r₂ ∈ (2s, 2+2s), NOT (2, 2s). If the exponent is sub-threshold the minimizer need not be unique.
- **Second-order condition φ''(1) < 0**: Every minimizer of J on P(a,b) satisfies φ''(u,v)(1) < 0 (the fiber map is concave at t=1 for a local minimum). This is a necessary geometric condition.
- **Scaling exponent arithmetic**: When computing α_p = p(1+s)/2 − 2, always expand fully — e.g. for the L²-supercritical threshold derive α_p > 2s algebraically: p(1+s)/2 − 2 > 2s → p > 2(1+3s)/(1+s). Do not guess the threshold.

## SOP — 5 Phases

### Phase A — Framework Card
1. Identify the Sobolev space (H^{1,s}, H¹, fractional Sobolev) and the space dimension.
2. Write the energy functional J explicitly: kinetic term + L^p nonlinearity + coupling term.
3. Identify the constraints: L²-norm preservation, mass pair (a,b), exponent relations (p,q,r₁,r₂,s).
4. Note which scaling transformation is used and what it preserves (L² norm).

### Phase B — Pohozaev Identity Check
1. Write the Pohozaev identity P(u,v) = 0 for the system.
2. **Key fact**: P(u,v) = 0 ⟺ (u,v) is a critical point of J in this framework (not just a necessary condition).
3. Verify that the Pohozaev manifold P = {(u,v) : P(u,v) = 0} is a natural constraint manifold.
4. Apply the fiber map: for any non-zero (u,v), the function t ↦ J(u_t, v_t) has a unique critical point t* > 0, and (u_{t*}, v_{t*}) ∈ P.

### Phase C — Energy Coercivity & Mountain Pass
1. For coercivity / boundedness from below: compute how J(u_t, v_t) scales as t → +∞ using the given scaling.
   - The kinetic term scales as t^{2s} (for H^{1,s} norm squared under the scaling).
   - The L^p term scales as t^{p(1+s)/2 - 2} (from mass-preservation scaling); denote α_p = p(1+s)/2 − 2.
   - J is unbounded from below iff α_p > 2s, i.e. p > 2(1+3s)/(1+s) — derive this inequality algebraically, do not guess.
   - When J is unbounded from below: the nonlinear term dominates as t→∞, making J(u_t,v_t)→−∞ → answer is True.
2. For mountain pass geometry: verify the Ambrosetti-Rabinowitz (AR) condition or the linking structure.
3. Mountain pass level c = inf_{γ∈Γ} max_{t∈[0,1]} J(γ(t)); if c > 0 and J satisfies (PS) condition, a critical point exists at level c.

### Phase D — Ground State & Uniqueness
1. **Mountain pass → ground state**: In the L²-constrained setting, the mountain pass critical point is the minimizer of J on P(a,b) → it is a positive ground state (by symmetric decreasing rearrangement or Schwarz symmetrization).
2. **Uniqueness**: The minimizer on P(a,b) is unique if and only if the coupling exponent satisfies r₁+r₂ ∈ (2s, 2+2s). For r₁+r₂ ∈ (2, 2s) uniqueness generally fails.
3. **Second-order condition**: To verify φ''(1) < 0, compute the fiber map φ(t) = J(u_t, v_t), differentiate twice, and evaluate at t*=1. The result takes the form φ''(1) = −2As(k−2s) where k is the nonlinear exponent sum and A > 0 is the kinetic-term coefficient. This is negative iff k > 2s — derive explicitly, do not state as a known fact without the computation.

### Phase E — Answer Normalization
1. Answer each sub-part strictly in the format the problem specifies: "(a) True/False; (b) Yes/No; (c) Yes/No."
2. Do not add prose explanation — only the verdict string.
3. Cross-check: if two sub-parts are logically linked (e.g., Pohozaev = 0 ↔ critical point AND mountain pass → ground state), ensure consistency.

## Subtype Playbook
- **Scaling / coercivity (sub-question type "True or false: J_t unbounded from below")**: Compute the dominant scaling exponent of the nonlinear term. If p > 2(1+3s)/(1+s) (the L²-supercritical threshold), kinetic term grows slower → unbounded: True.
- **Pohozaev manifold membership (sub-question type "P(u,v)=0 implies critical point")**: Yes — this is an equivalence in the anisotropic coupled system, not just implication.
- **Fiber map uniqueness (sub-question type "exists unique t > 0 such that (u_t,v_t) ∈ P")**: Yes — standard fiber map result for Pohozaev-type manifolds.
- **Mountain pass → ground state (sub-question type "critical point implies positive ground state")**: Yes — in the L²-norm-preserving constrained framework.
- **Minimizer uniqueness (sub-question type "minimization over P(a,b) yields unique solution")**: depends on exponent range. For r₁+r₂ ∈ (2, 2s): No. For r₁+r₂ ∈ (2s, 2+2s): Yes.
- **Second-order condition φ''(1) < 0**: Yes — necessary condition for being a minimizer.

## Output Rules
- Multi-part (a)/(b)/(c): use exact format "(a) True/False; (b) Yes/No; (c) Yes/No." — match separator style from the problem.
- No markdown fences, no explanatory prose.
- If a sub-part yields a formula (not just Yes/No), use the problem's variable names exactly.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source task IDs.
