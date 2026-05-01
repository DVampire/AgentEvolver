---
name: theoretical_physics_modeling_skill
description: Derive closed-form observables for theoretical physics systems (partition functions, FET electrostatics, orbital mechanics, stabilizer formalism, N=4 SYM, scattering cross-sections) from first-principles Hamiltonians/Lagrangians; also solve biophysical ODE systems (synaptic plasticity, dendritic models, Hebbian learning rules) for steady-state expressions and fixed points.
type: sop
version: 1.1.0
require_grad: true
---

# Theoretical Physics Modeling Skill

## Goal
For theoretical-physics systems, derive the observable symbolically or numerically from a first-principles Hamiltonian / Lagrangian.

## When to Activate

**Domain 1 — Theoretical Physics**
- The problem gives a Hamiltonian / Lagrangian / path integral / orbital parameters / stabilizer generators / scattering apparatus.
- The answer is a symbolic closed form (Z=Tr e^{-βH}, σ/σ_C ratio, (C_tg V_tg − C_bg V_bg)/2) or an exact ratio.
- The problem mixes grand canonical ensembles / classical-quantum regimes / large-N limits / small-angle approximations.

**Domain 2 — Biophysical ODE Systems (Synaptic / Dendritic Plasticity)**
- The problem defines a multi-variable coupled ODE system with biophysical variable names (presynaptic accumulator, postsynaptic accumulator, synaptic weight, learning rate).
- The problem asks for steady-state expressions, fixed points, or perturbation responses of the system.
- Key vocabulary: dendritic plasticity, synaptic weight, accumulator, Hebbian learning, STDP, fixed point, steady state.

## Common Failure Modes (Avoid These)

*Domain 1 — Theoretical Physics*
- Substituting into a formula without first choosing an ensemble / gauge / renormalization scheme.
- For stabilizer or path-integral problems, dropping a constant factor or logarithm.
- On scattering problems, truncating the small-θ expansion at the wrong order.
- For transit / occultation problems: compute the flux drop δ = (r_planet/R_star)² first, then convert to magnitude via Δm = −2.5 log₁₀(1−δ) — do not directly estimate Δm.

*Domain 2 — Biophysical ODE Systems*
- Numerically evaluating the steady-state instead of solving symbolically — when the problem asks for an expression in terms of model parameters, keep the result symbolic.
- Setting only some derivatives to zero instead of all simultaneously when finding the fixed point.

## SOP — 5 Phases (phase count tailored to this skill's natural workflow)

### Phase 1 — System & Framework Declaration
1. Write down the Hamiltonian / Lagrangian / path integral; list degrees of freedom, symmetries, conserved quantities.
2. Pick the ensemble (canonical / grand canonical / microcanonical) and the quantization scheme (first / second / path integral) explicitly.
3. Declare the approximation level (classical limit, weak coupling, small angle, large N).

### Phase 2 — Standard-Machinery Application
1. Partition function → path integral + energy-level sum; stabilizer → symplectic counting; scattering → Born approximation or phase shifts.
2. For gauge theories, make the gauge choice and the ghost-term treatment explicit.
3. For N=4 SYM, keep the problem's field and coupling names verbatim; do not silently re-label indices.

### Phase 3 — Observable Derivation
1. Produce Z / free energy / polarizability / differential cross-section as intermediate quantities.
2. For scattering ratios, write both θ's full expression first, then do a small-θ expansion keeping leading order.
3. For stabilizer counting, use the Z_2^{2n} invariant-form + commutation constraint dimension equation.
4. For transit / occultation photometry: (a) derive geometric ratio r_planet/R_star from angular-size or speed constraints; (b) compute transit depth δ = (r_planet/R_star)²; (c) convert to magnitude loss Δm = −2.5 log₁₀(1−δ). Execute each step symbolically before plugging in numbers.

### Phase 4 — Limit & Dimension Check
1. Limit checks: β→0 / β→∞ / θ→0 / large N / single-gate limit / monopole charge → 0.
2. Dimensional analysis: every factor has consistent units.
3. Compare against known special cases (pure Rutherford, single-gate FET, ideal-gas limit).

### Phase 5 — Symbolic Answer Output
1. Preserve the problem's subscript naming ($C_{tg}$, $V_{bg}$, $\mu$).
2. Fractions / square roots follow the problem's convention ($\frac{\sqrt{2}}{2}$ rather than $\frac{1}{\sqrt{2}}$ when that is what the problem uses).
3. Multi-part answers follow the problem's ordering and separators exactly.

## Playbook

### Domain 1 — Theoretical Physics
- **Grand canonical Z**: $Z = \mathrm{Tr}\, e^{-\beta H}$; $H = -\mu \hat N$ flips the exponent to $e^{+\beta \mu \hat N}$. Output the canonical symbolic form $Z=\mathrm{Tr}\exp(-\beta\hat{H})$ unless the problem explicitly asks for substitution.
- **FET electrostatics**: superpose top-gate / back-gate contributions, then divide by 2.
- **Transit / occultation photometry**: set up Keplerian geometry → derive angular-size ratio r/R → compute δ = (r/R)² → Δm = −2.5 log₁₀(1−δ). Never estimate Δm directly.
- **Brown-dwarf orbital mechanics**: parabolic vs circular velocity comparison; escape $v = \sqrt{2GM/R}$.
- **Stabilizer formalism**: destabilizer count $2^{n(n+3)/2}$-type results, derived from $\mathbb{Z}_2$-matrix dimensions.
- **N=4 SYM**: follow the problem's Lagrangian index convention strictly.
- **Monopole scattering**: $1/\sin^4(\theta/2)$-type cross-section; ratio against Rutherford.

### Domain 2 — Biophysical ODE Systems
- **Steady-state of coupled linear ODEs**: set ẋ = 0 for every variable simultaneously; substitute back to obtain a linear algebraic system; solve for steady-state expressions in terms of model parameters. Compute symbolically — do not plug in numbers.
- **Fixed-point derivation for plasticity models**: identify the fixed point by setting all accumulator derivatives to zero; the learning rule's fixed point is the synaptic weight value where net plasticity = 0. Express using the parameter names from the problem (τ, φ, μ, ρ, etc.).

## Output Rules
- Preserve the problem's subscript names ($C_{tg}$, $V_{bg}$, etc.).
- Multi-part answers are given in the problem's order, comma separated.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
