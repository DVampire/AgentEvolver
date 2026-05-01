---
name: markov_hitting_time_skill
description: Compute expected hitting times, pattern waiting times, and escape/absorption probabilities for Markov chains. Activate for problems asking "expected steps/rolls/flips until pattern X appears", "probability of reaching A before B", or "first passage time" on random walks.
type: sop
---

# Markov Chain Hitting Time & Pattern Probability Skill

## Goal
For stochastic problems that require computing an expected hitting/stopping time, escape probability, or pattern-appearance probability, produce an exact closed-form or high-precision numerical answer by building and solving a finite Markov chain recurrence.

## When to Activate
- Problem asks for the **expected number of steps/rolls/trials until** a specific sequence, pattern, or event first occurs.
- Problem asks for the **probability of escape** from a region, or the probability of reaching a target before another state (gambler's ruin variants).
- Problem involves **continuous-time random walks** on lattices/graphs and asks for escape probability or expected time.
- Key vocabulary: "expected time", "expected number of rolls/flips/steps until", "probability to escape", "first passage time", "hitting time", "absorption probability", "waiting time for pattern".

## Do NOT Activate If
- The question is a static probability (no sequential process) — just apply conditional probability directly.
- The question is about an ODE/PDE system (use `differential_equation_integration_skill`).
- The question is about an abstract group/algebraic structure with no random walk.

## Common Failure Modes (Avoid These)
- For pattern-matching problems, using a naïve geometric series (ignoring overlaps) instead of the Conway/Markov chain approach.
- For multi-particle systems, treating particles as independent when they interact (e.g., coalescing random walks).
- For continuous-time walks, applying discrete-time formulas without adjusting for exponential holding times.
- Forgetting that on a torus or bounded domain, boundary conditions (periodic vs absorbing) fundamentally change the answer.
- For "expected time until pattern P", missing the autocorrelation structure of P — overlapping prefixes reduce the expected time.

## SOP — 4 Phases

> **MANDATORY**: Every phase requires at least one `bash_tool` call producing observable output. Do NOT advance to the next phase by reasoning alone.

### Phase 1 — Classify & Run Script
1. Identify the problem subtype: pattern-matching / hitting time / escape probability / continuous-time walk.
2. **REQUIRED — run the solver script immediately** with the appropriate subcommand:
   - **Pattern-matching** (expected steps until string P appears):
     ```bash
     python scripts/markov_solver.py pattern <PATTERN> --alphabet <K> --method both
     ```
     Example (pattern "TENETENET", 26-letter alphabet):
     ```bash
     python scripts/markov_solver.py pattern TENETENET --alphabet 26 --method both
     ```
   - **General hitting time** (from transition matrix):
     ```bash
     python scripts/markov_solver.py hitting --transitions '<JSON_MATRIX>' --absorbing '<JSON_LIST>' --initial <STATE_IDX>
     ```
     Example (3-state chain, state 2 absorbing, start at state 0):
     ```bash
     python scripts/markov_solver.py hitting --transitions '[[0,0.5,0.5],[0,0.5,0.5],[0,0,1]]' --absorbing '[2]' --initial 0
     ```
   - **Escape probability**:
     ```bash
     python scripts/markov_solver.py escape --transitions '<JSON_MATRIX>' --target '<JSON_LIST>' --absorbing '<JSON_LIST>' --initial <STATE_IDX>
     ```
     Example (gambler's ruin, 4 states, absorbed at 0 or 3, start at 1):
     ```bash
     python scripts/markov_solver.py escape --transitions '[[1,0,0,0],[0.5,0,0.5,0],[0,0.5,0,0.5],[0,0,0,1]]' --target '[3]' --absorbing '[0,3]' --initial 1
     ```
   Record the script output as the Phase 1 artifact.

### Phase 2 — Recurrence System or Matrix Inversion
**For expected hitting times (discrete time):**
1. For each non-absorbing state s, write: `E[T | s] = 1 + Σ_{s'} T[s,s'] E[T | s']`
2. Rearrange into a linear system `(I - Q) x = 1` where Q is the sub-matrix of T restricted to non-absorbing states.
3. Solve with Python: `x = np.linalg.solve(I - Q, ones)` or sympy for exact rational arithmetic.

**For escape/absorption probabilities:**
1. For each non-absorbing state s: `P[reach A | s] = Σ_{s'} T[s,s'] P[reach A | s']` with boundary conditions.
2. Solve the linear system.

**For continuous-time walks:**
1. Use the generator Q. The mean hitting time satisfies `Q̃ h = -1` where Q̃ is Q restricted to non-absorbing states.
2. Equivalently, solve via the discrete-time embedded chain + exponential holding time correction.

### Phase 3 — Solve & Extract
1. Run the linear system in Python (sympy for exact fractions when needed, numpy for large systems).
2. Extract the value at the initial state s₀.
3. For multi-particle systems: if particles are independent, the joint hitting time distribution is the maximum (or minimum) of individual hitting times — compute via inclusion-exclusion or generating functions.
4. For particles with interaction (coalescence, annihilation): treat the joint system as one large Markov chain; state = full configuration.

### Phase 4 — Verification & Format
1. **REQUIRED — Monte Carlo cross-check** (for pattern problems):
   ```bash
   python scripts/markov_solver.py simulate pattern <PATTERN> --alphabet <K> --runs 100000
   ```
   Example:
   ```bash
   python scripts/markov_solver.py simulate pattern TENETENET --alphabet 26 --runs 100000
   ```
   Confirm the simulated mean is within 1% of the Phase 1 result. Record agreement as the Phase 4 artifact.
2. Output the answer as a bare integer or exact fraction matching the problem's format.

## Subtype Playbook

- **Pattern matching (uniform alphabet, size k)**: States = prefix lengths 0..n. Conway formula: `E[T] = Σᵢ₌₁ⁿ kⁱ · [P[1..i] == P[n-i+1..n]]`. For "TENETENET" over 26 letters: the overlapping prefix structure is {1,5,9} → E[T] = 26^9 + 26^5 + 26^1.
- **Alternating dice pattern** (e.g., a₁ of face X, a₂ of face Y, …): Build a state machine tracking current position in the required sequence; state = (which symbol group, how many of current symbol collected). Transition from state (i,j) on a matching roll: go to (i,j+1); on wrong roll: reset appropriately.
- **Gambler's ruin / 1D absorbing walk**: For walk on {0,1,...,N} absorbed at 0 and N, E[Tₓ] = x(N-x) for symmetric walk. For biased p≠1/2, use the standard formula: E[Tₓ] = [N/(q-p)] · [1-(q/p)ˣ]/[1-(q/p)^N] - x/(q-p).
- **Random walk escape from a 3D cube** [0,2n]³: log(1/pₙ)/log(n) → 2/3 from the known result for 3D random walk capacity.
- **Continuous-time multi-particle coalescence**: Use duality; the expected coalescence time of k particles on ℤ with rate λ walk is O(1/λ) when particles interact on contact.
- **Discrete torus random walk hitting**: Use the Green's function / harmonic measure; for 2D torus of size n, the hitting probability of two adjacent vertices is asymptotically e^{-π/2}.
- **Epidemic/transmission model R0 derivation**: if the problem defines compartmental rates (burning/recovery rates, contact rates, population sizes) and asks for a basic reproduction number R0, derive it as a next-generation operator product. For a two-population (e.g. tree–grass) fire/epidemic spread model, R0 = (transmission_rate × contact_rate × target_density) / (removal_rate × source_density) — derive each factor from the model's ODE terms directly. This is NOT a Markov hitting-time problem; route to an ODE analysis subtype instead.

## Python Tool Usage Notes
- Use `sympy.Rational` for exact arithmetic when the transition probabilities are rational.
- For patterns with state space > 50, switch to `numpy.linalg.solve` with float64; verify with a few exact sympy entries.
- When building the transition matrix, assert that each row sums to 1.0 before proceeding.
- For the Conway formula, implement as: `sum(k**i for i in range(1, n+1) if P[:i] == P[n-i:])` where P is the target string.

## Output Rules
- Exact integers: bare numeral (e.g., `5429515560378`).
- Exact fractions: use the problem's LaTeX notation.
- Closed-form expressions: use the variable names from the problem.
- Multi-part answers: follow the order and separator from the problem.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source task IDs.
