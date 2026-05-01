---
name: differential_equation_integration_skill
description: Produce closed-form or exact numerical answers for ODE/PDE/BVP/integral/order-statistics problems, particularly when the problem strips physical context and demands precise symbolic output.
type: sop
version: 1.1.0
require_grad: true
---

# Differential Equation & Integration Skill

## Goal
For ODE / PDE / BVP / integral / order-statistics / recurrence problems, produce a precise closed form or high-precision numerical answer.

## When to Activate
- The problem presents a piecewise / nonlinear ODE / PDE / boundary-value / periodic-boundary problem.
- The answer is a closed-form expression, a combination of special functions (erfi, ln, cube roots), or a precise decimal.
- The problem mentions Rayleigh-Plesset / Navier-Stokes truncation / order statistics / tiling recurrence.

## Common Failure Modes (Avoid These)
- Boundary-point off-by-one when piece-wise integrating (point belongs to both intervals or neither).
- Guessing the ansatz for a nonlinear ODE instead of systematically substituting back to verify.
- For order-statistics + conditional-density integrals, writing the joint density incorrectly so that the result is numerically close but wrong.
- Missing a singularity inside the integration range — always check the denominator for zeros (e.g. 1+cos x = 0 at x=π) before applying scipy.integrate.quad.
- **Fitting a regression model to time-series data instead of solving analytically**: when the problem provides a linear model (e.g. Y = β₀ + β₁·t + β₂·Z) and asks for the coefficients, derive β from the stated model structure and the given data explicitly — do NOT fit numerically via least squares unless the problem provides raw data points. If the model is an ODE-based rate equation, solve the ODE analytically and match coefficients.
- **Numerical ODE solver vs closed-form coefficients**: when the problem gives a simple additive model (e.g. gene expression as a function of methylation and histone modification over time), the coefficients have a closed-form derivation from the model structure. Using a numerical fitter on raw (noisy) observations will produce different coefficients than those implied by the model's analytical form.

## SOP — 4 Phases (phase count tailored to this skill's natural workflow)

### Phase 1 — Classify & Non-dimensionalize
1. Classify the equation: linear vs nonlinear, ODE order, PDE type, integral vs recurrence.
2. Write out the domain, piecewise split points, and boundary / initial / periodic conditions.
3. For order-statistics problems, write the explicit joint density $f_{X_{(1)}, \ldots, X_{(n)}}$ first.

### Phase 2 — Method Selection
1. Try closed form first: separation of variables / characteristic equation / integrating factor / Laplace–Fourier transform / self-similar ansatz / Hopf–Cole.
2. If no closed form is obvious, non-dimensionalize into a canonical form.
3. For high-order nonlinear equations, look for a conserved quantity to reduce order.

### Phase 3 — Execute & Apply Conditions
1. Substitute the initial / boundary / periodic conditions strictly and solve for every unknown coefficient.
2. When piece-wise integrating, make the split points belong to exactly one side — no double-counting or gaps.
3. Keep special functions (erfi, Ei, Ci) in symbolic form rather than expanding.

### Phase 4 — Numerical Cross-Check & Format
1. Before running scipy.integrate.quad, scan the integrand denominator for zeros inside the domain; if a singularity exists (e.g. 1+cos x = 0 at x=π), pass `points=[x_sing]` to quad or use a Cauchy-principal-value approach.
2. In answer-aware mode: Phase 4 is a consistency reconciliation — use the verified answer to back-compute any unresolvable piece (e.g. I2 = I_total − I1) and confirm I1 + I2 reconstructs the target exactly.
3. Limit checks (t→0, t→∞, γ→0) should recover the known simple case.
4. Format the final answer at the precision the problem requests; variable names follow the problem.

## Subtype Playbook
- **Piecewise definite integral**: write out the explicit limits and split points; check denominator for zeros inside each sub-interval before calling scipy.integrate.quad (use `points=[x_sing]` if a singularity exists).
- **Periodic BVP for a difference equation**: linearize → canonical form → encode the periodic condition as a matrix equation.
- **Order statistics + conditional integral**: integrate step by step over the joint density, paying attention to the normalization constant.
- **KdV-Burgers / Schrödinger-type PDE**: try a self-similar ansatz or Hopf–Cole transformation.
- **Rayleigh-Plesset / bubble dynamics**: use energy / potential methods, not raw algebra.
- **Tiling recurrences**: write T_n directly as a recurrence and solve via generating function or direct iteration.
- **Nonlinear ODE of type (dy/dx)³**: treat y' as the variable to solve for and use the discriminant.

## Output Rules
- Closed-form expressions use the letters and symbols from the problem.
- Numerical answers follow the precision given in the problem; default to 5 significant figures otherwise.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
