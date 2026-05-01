---
name: discrete_linear_system_skill
description: Solve discrete-time linear systems and difference equations. Activate for problems involving difference equations with closed-form solutions, deadbeat observer/controller design, observer/controller canonical form reduction, or z-transform analysis of discrete-time state-space systems.
type: sop
---

# Discrete Linear System & Difference Equation Skill

## Goal
For discrete-time linear systems and difference equations, produce exact closed-form solutions (difference equations), exact gain matrices (observer/controller design), or canonical-form state-space representations.

## When to Activate
- Problem presents a **difference equation** `Σ aₖ y[n-k] = f[n]` and asks for a closed-form solution given initial conditions.
- Problem presents a **discrete-time state-space system** x[n+1] = Ax[n] + Bu[n], y[n] = Cx[n] and asks for observer design, controller design, or canonical-form reduction.
- Problem asks to design a **deadbeat observer** (observation error reaches zero in finite steps) or a **deadbeat controller**.
- Problem asks to **reduce a system to observer/controller canonical form**.
- Key vocabulary: "difference equation", "closed-form solution", "observer canonical form", "controller canonical form", "deadbeat", "observation error identically zero", "gain matrix", "discrete-time system", "transfer function", "z-transform".

## Do NOT Activate If
- The system is continuous-time (use `differential_equation_integration_skill`).
- The question is about complexity / computability of algorithms (use `cs_algorithm_complexity_skill`).
- The question is about abstract linear algebra without a dynamical system context.

## Common Failure Modes (Avoid These)
- For difference equations, forgetting to find the **particular solution** when the RHS is non-zero, and only computing the homogeneous solution.
- For deadbeat observer design: choosing the observer gain L such that (A-LC) has eigenvalues near zero rather than **exactly zero** (deadbeat requires all eigenvalues at 0).
- For canonical form reduction: applying the continuous-time transformation formulas instead of the discrete-time ones.
- For difference equations with repeated characteristic roots: using the wrong form of the general solution (missing n·rⁿ terms).
- Off-by-one with initial conditions: y[0] and y[-1] are given — correctly substitute into the solution formula to pin constants.

## SOP — 4 Phases

> **MANDATORY**: Every phase requires at least one `bash_tool` call producing observable output. Do NOT advance to the next phase by reasoning alone.

### Phase 1 — Classify & Run Script
1. Identify the problem subtype: difference equation / deadbeat observer / canonical form reduction.
2. **REQUIRED — run the solver script immediately**:
   - **Difference equation** (`a0·y[n] + a1·y[n-1] + ... = f[n]`):
     ```bash
     python scripts/discrete_system_solver.py diffeq --coeffs 'a0,a1,...,am' --rhs 'constant:K' --init 'y0=V0,ym1=Vm1' --steps 10
     ```
     Example (`8y[n] - 6y[n-1] + y[n-2] = 1`, y[0]=1, y[-1]=2):
     ```bash
     python scripts/discrete_system_solver.py diffeq --coeffs '8,-6,1' --rhs 'constant:1' --init 'y0=1,ym1=2' --steps 10
     ```
   - **Deadbeat observer design**:
     ```bash
     python scripts/discrete_system_solver.py deadbeat --A '<JSON_MATRIX>' --C '<JSON_MATRIX>'
     ```
     Example (2×2 system):
     ```bash
     python scripts/discrete_system_solver.py deadbeat --A '[[0,1],[−0.5,−1]]' --C '[[1,0]]'
     ```
   - **Canonical form reduction**:
     ```bash
     python scripts/discrete_system_solver.py canonical --A '<JSON_MATRIX>' --B '<JSON_MATRIX>' --C '<JSON_MATRIX>' --form observer
     ```
     Example:
     ```bash
     python scripts/discrete_system_solver.py canonical --A '[[0,1],[−0.5,−1]]' --B '[[0],[1]]' --C '[[1,0]]' --form observer
     ```
   - **Stability / characteristic roots check**:
     ```bash
     python scripts/discrete_system_solver.py stability --A '<JSON_MATRIX>'
     ```
     Example:
     ```bash
     python scripts/discrete_system_solver.py stability --A '[[0,1],[−0.5,−1]]'
     ```
   Record the script output as the Phase 1 artifact.

### Phase 2 — Interpret & Derive Closed Form (Difference Equations)
1. From the script output, read the characteristic roots and particular solution.
2. Write the general solution: for each root rᵢ with multiplicity mᵢ, contribution = `(c_{i,0} + c_{i,1}·n + …)·rᵢⁿ`, plus particular term.
3. Apply initial conditions to solve for constants — use `python_interpreter_tool` with sympy if needed.
4. Write out the final closed-form expression.

### Phase 3 — State-Space Design (Observer / Controller)
**Deadbeat observer design:**
1. Goal: choose L (m×p matrix) such that all eigenvalues of (A - LC) equal **exactly 0**.
2. The characteristic polynomial of (A - LC) must equal zⁿ (all roots at origin).
3. Method: use the **Ackermann formula** for SISO or direct pole-placement for MIMO:
   - Desired polynomial: p(z) = zⁿ
   - Compute L via `scipy.signal.place_poles` with all poles at 0, or via direct symbolic computation.
4. **Minimum number of steps for deadbeat**: equals the observability index (smallest k such that observability matrix has rank n).
5. **Run deadbeat design**:
   ```bash
   python scripts/discrete_system_solver.py deadbeat --A '<JSON_MATRIX>' --C '<JSON_MATRIX>'
   ```
6. **Run general observer (pole placement)**:
   ```bash
   python scripts/discrete_system_solver.py observer --A '<JSON_MATRIX>' --C '<JSON_MATRIX>' --poles '[p1,p2,...]'
   ```

**Observer canonical form:**
1. Compute the characteristic polynomial of A: `det(zI - A) = zⁿ + a₁zⁿ⁻¹ + … + aₙ`.
2. The observer canonical form matrices are:
   - Ã = [[0, 0, …, 0, -aₙ], [1, 0, …, 0, -aₙ₋₁], …, [0, 0, …, 1, -a₁]]
   - B̃ = transformation of B
   - C̃ = [0, 0, …, 0, 1] (or as derived from observability transform)
3. Transformation matrix T = O⁻¹ Õ where O and Õ are observability matrices of original and canonical system.
4. **Run canonical form reduction**:
   ```bash
   python scripts/discrete_system_solver.py canonical --A '<JSON_MATRIX>' --B '<JSON_MATRIX>' --C '<JSON_MATRIX>' --form observer
   ```

**Controller canonical form:**
1. Compute characteristic polynomial similarly.
2. Canonical form via controllability matrix transformation.
3. **Run canonical form reduction**:
   ```bash
   python scripts/discrete_system_solver.py canonical --A '<JSON_MATRIX>' --B '<JSON_MATRIX>' --C '<JSON_MATRIX>' --form controller
   ```

### Phase 4 — Verification & Format
1. **Difference equation**: Substitute the closed-form back into the original equation and check for n=0,1,2.
   ```bash
   python scripts/discrete_system_solver.py verify --diffeq 'a0,a1,...' --solution 'A,B,C,D,E' --init 'y0=V0,ym1=Vm1' --steps 5
   ```
2. **Observer**: Compute eigenvalues of (A - LC) using `numpy.linalg.eigvals` and verify all are 0 (or the desired poles).
3. **Canonical form**: Verify Ã = T A T⁻¹, B̃ = T B, C̃ = C T⁻¹ using Python matrix multiplication.
4. Format the answer:
   - Closed-form: write as `y[n] = A(B)ⁿ + C(D)ⁿ + E` matching the problem's template.
   - Gain matrix: as a matrix with the same row/column structure as the problem specifies.
   - Canonical form: list the transformed A, B, C matrices.

## Subtype Playbook

- **2nd-order difference equation with distinct real roots** (`8y[n] - 6y[n-1] + y[n-2] = 1`):
  - Characteristic roots: `8r² - 6r + 1 = 0` → `r = (1/2), (1/4)`.
  - Particular solution (constant RHS): `y_p = K`, plug in: `8K - 6K + K = 1` → `K = 1/3`.
  - General: `y[n] = c₁(1/2)ⁿ + c₂(1/4)ⁿ + 1/3`.
  - Apply `y[0]=1, y[-1]=2` → solve for c₁, c₂.

- **Deadbeat observer for n-th order MIMO system**:
  - Minimum steps = observability index ν = smallest k with rank(O_k) = n.
  - All eigenvalues of (A-LC) placed at 0 via Ackermann or `scipy.signal.place_poles(A.T, C.T, [0]*n).gain_matrix.T`.

- **z-transform approach**: For one-sided z-transform, `Y(z) = H(z)·U(z) + I(z)` where I(z) encodes initial conditions. Partial-fraction expand and inverse-transform.

- **Stability check before design**: Discrete-time system is stable iff all eigenvalues of A lie strictly inside the unit circle. Deadbeat places all at 0 (guaranteed stable).

## Python Tool Usage Notes
- Use `sympy.symbols`, `sympy.solve`, `sympy.roots` for exact characteristic root computation.
- Use `numpy.linalg.matrix_rank` for controllability/observability matrix rank checks.
- Use `scipy.signal.place_poles` for pole placement (pass transpose for observer design).
- When sympy cannot factor the characteristic polynomial, fall back to `numpy.roots` for numerical roots and then rationalize.
- Always print intermediate matrices (transition matrix, observability matrix) as Python outputs so they appear in the trace.

## Output Rules
- Closed-form: use the exact template the problem provides (e.g., `y[n] = A(B)ⁿ + C(D)ⁿ + E`).
- Gain matrix: list as a matrix, rows separated by semicolons or newlines matching the problem's notation.
- Integer answers (e.g., minimum number of steps): bare numeral.
- Multi-part: follow problem's ordering and separator exactly.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source task IDs.
