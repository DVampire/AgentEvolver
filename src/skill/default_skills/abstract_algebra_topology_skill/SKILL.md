---
name: abstract_algebra_topology_skill
description: solve problems about abstract algebraic/topological structures (representations, set theory, k-theory, dessins, quadratic forms, continua, matrix semigroups). activates when the problem hinges on axiomatic definitions and demands an exact invariant, count, or true/false verdict.
type: sop
version: 1.2.3
require_grad: true
---

# Abstract Algebra & Topology Skill

## Goal
For problems about abstract algebra / topology / axiomatic mathematical objects, produce a formally correct, normalized unique answer (integer / closed form / "Yes-No" / tuple).

## When to Activate
- Problem statement contains abstract-structure terms such as representation / category / dessin / continuum / K-group / BCH / quadratic form / omega-cardinal / matrix semigroup.
- A precise symbolic / integer / closed-form answer is required ("count", "smallest N", "True/False/Yes/No").
- The problem first defines its own objects in a paragraph of prose and then asks for a quantity about those defined objects.

## Common Failure Modes (Avoid These)
- Treating a local property as if it were a global one, or skipping a constraint emphasized in the problem text.
- Applying a similar-looking but subtly wrong structure theorem (e.g. using classical Lie-algebra results on a quantum group or a dessin).
- Producing an expression that is close to correct but not normalized into the unique form the problem demands (missing coefficient, inconsistent sign).
- Replacing the actual property asked in the problem by a looser proxy that is easier to enumerate or test computationally.

## SOP — 5 Phases (phase count tailored to this skill's natural workflow)

### Phase A — Object Card
1. Formalize the abstract object, its action/operation, and the stated constraints into a structured object-card: {domain, structure, morphisms, constraints, invariant_asked}.
2. Distinguish meta-language from object-language: for example in ZFC every variable ranges over sets — do not silently promote a proper class to a set.
3. Write out every explicit condition in the problem as a **numbered atomic list** `C1, C2, …, Cn`. Each `Ci` must be a single non-conjunctive clause — if the problem states "G is face-quasiprimitive; D is regular; D smoothly covers a unicellular regular dessin", produce three separate `Ci` entries, not one merged phrase. Treat parameter-fixing clauses ("q is a primitive 3rd root", "d = 14", "N is minimal normal") as their own `Ci`. Redundancy is expected; do **not** collapse near-synonyms or merge compound conditions at this stage — Phase D depends on the full atomized list being visible column-by-column. If a condition looks redundant, assume it is not and keep it.

### Phase B — Structure Theorem Match
1. Match the applicable classification / structure theorem (classification of finite abelian groups, Schur's lemma, Krull–Schmidt, Kunen inconsistency, Bökstedt–Rognes K-theory tables, Springer / Witt decomposition, dessin classification, Myhill–Nerode).
2. If a known classification applies, use it directly — do NOT rederive from axioms.
3. For any intuition-driven answer, explicitly write the implication chain "if the answer is X then it must follow that ..." and discard it if inconsistent.
4. Do not collapse a theorem-level classification question into a simpler surrogate predicate unless the problem itself or the matched theorem justifies that reduction.
5. When citing a classification / structure theorem, the Phase B artifact must record the theorem's **full hypothesis block**, not only its conclusion. Write it in the explicit form `Theorem T: if (H1 ∧ H2 ∧ … ∧ Hk) then Conclusion`, where each `Hi` is atomic. A citation that only names the theorem and restates its conclusion ("types with a regular minimal normal subgroup are HA/HS/HC/TW") is **invalid** — every `Hi` must be stated. Then, in the same artifact, line every `Hi` against the Phase A constraints `Cj` and mark which `Cj` discharges it; any `Hi` left unmatched is an open assumption the theorem cannot be applied on, and you must either find the matching `Cj` or pick a different theorem. This prevents using a theorem's conclusion as a filter while silently skipping one of its hypotheses.
6. **Reverse audit: every `Cj` from Phase A must be examined against the cited theorem.** After mapping Hi→Cj per point 5, iterate over the **full Phase A list C1..Cn** and for each `Cj` answer exactly one of:
   (a) **used** — `Cj` already discharges some `Hi` (point to which);
   (b) **refines** — `Cj` is strictly stronger than whatever `Hi` it most closely matches; in this case the cited theorem is the **wrong (too general) version**: you must either append a new `Hk+1` matching `Cj` (producing a stronger, more specific theorem that yields a **tighter** conclusion, possibly with fewer allowed cases), or switch to a sharper named theorem that already has `Hk+1`. Do not keep the weaker theorem "because its hypotheses all check out" — the fact that `Cj` is extra information the theorem ignored is itself evidence the wrong version was invoked.
   (c) **decorative** — `Cj` is purely definitional or part of the problem's prose without operational content (e.g. naming conventions). This requires an explicit one-line justification for why `Cj` cannot possibly refine any `Hi` of the cited theorem.
   Any `Cj` whose answer is left blank counts as option (b) by default — silently dropping a problem condition is the most frequent path to a wrong answer in this skill's traces.

### Phase C — Derivation or Enumeration
1. For counting / existence problems, prefer Python + sympy / itertools to enumerate small cases, then generalize. Run the enumeration as a tool call — do not mental-count.
2. For statements inside a formal system, compare consistency strengths explicitly and cite the corresponding theorem (Kunen, Solovay, etc.).
3. For homology / K-theory problems, write down the known periodicity / Bott structure first, then plug in the parameters.
4. For variational / scaling problems that ask qualitative True/False: compute the dominant scaling exponent of each term explicitly (kinetic t^{2s} vs nonlinear t^{α}), then compare — do not guess from intuition.

### Phase D — Boundary & Cross-Theorem Check
1. Plug in extreme cases (n=0,1, trivial representation, 0-dimensional, empty set) and verify.
2. For "smallest N such that property P holds" problems, both construct an instance at N and prove N−1 fails.
3. If the same quantity can be computed via two different theorems, the results must agree; otherwise return to Phase B.
4. The Phase D verification artifact is **one explicit two-axis grid**, constructed via a Python tool call so it is observable in the trace:
   - **Rows** —
     - for candidate-filtering problems ("which elements/types from a given set satisfy property P"): each row is one candidate from the problem's provided set (or enumerated in Phase B);
     - for computation problems without an explicit candidate set (counts, expectations, closed-form values): each row is one **independent derivation path** for the quantity — a distinct formula, modeling assumption, theorem application, or small-scale enumeration. At least two rows per quantity are required.
   - **Columns** —
     - for candidate-filtering problems: the atomic constraints `C1, …, Cn` from Phase A, one column per `Ci`;
     - for computation problems: the quantities asked by the problem, one column per quantity.
   - **Cells** — each cell contains exactly one of: `✓ <reason tied to Ci or to the path>`, `✗ <reason>`, or `N/A <why this Ci does not apply to this candidate>`. A blank cell is an analysis gap — regenerate the grid, do **not** fall back to a single-sentence justification that covers multiple columns at once.
   - **Reading the answer from the grid** —
     - candidate-filtering: the included set is exactly the rows with no `✗` cell; every `✓` and `N/A` must cite the specific `Ci` or theorem clause it relies on;
     - computation: the reported value for each quantity requires **intra-column agreement across ≥ 2 rows**; if rows disagree, the disagreement itself is the Phase D finding and Phase B must be revisited (not averaged, not silently picked).
   - **When a path/tool call fails** (timeout, missing package, OOM): this does not excuse leaving a blank row. Substitute a lower-cost path — reduce problem size, switch to a different theorem, or use a coarser bound — and record it as a row. An unverified single-path answer is not a valid Phase D output.
5. A candidate-classification or computed value is not complete unless every `✓ / ✗ / N/A` cell in the grid is traceable to an explicit `Ci` from Phase A, a matched theorem from Phase B, or a concrete artifact from Phase C.
6. **Column completeness self-check (mandatory).** The Python tool call that builds the grid must conclude with an assertion equivalent to `assert all(set(row.keys()) == set(declared_columns) for row in grid)` and print the assertion outcome. Declaring columns `cols = [C1..Cn]` but filling only some keys in each row (e.g. omitting `C7, C8` because they "always apply globally") is **not** a valid pass — this silently hides Phase A constraints from the check and is a recurring way the grid has been gamed in past traces. If any constraint `Cj` would genuinely apply uniformly to every candidate row (a common, non-differentiating column), still write its cell explicitly as `✓ uniformly satisfied by all candidates because …` — do not drop the column. The verification artifact is only acceptable when `assert` passes and the printed grid shows one cell per (row, `Cj`) pair.

### Phase E — Answer Normalization
1. Produce the answer in the format the problem demands (integer / closed form / "Yes"/"No" / tuple / multiple-choice letter).
2. Use the variable names from the problem (T, z, N, k — do not silently rename to n, m).
3. The final answer carries no markdown, no code fences, no prose explanation.

## Subtype Playbook
- **Representation theory / quantum groups**: list the dimensions of irreducible representations and the decomposition rules, then count.
- **Set theory / large cardinals**: compare consistency strengths; use Kunen inconsistency to check j: V→V-style statements.
- **K-theory of rings**: locate the parameter block using the known K_n(\mathbb{Z}/p^k) tables and periodicity.
- **Dessins / bipartite maps**: list the normal-subgroup lattice of the 2-generated group and filter with the faithful-action condition. For O'Nan–Scott-type or quasiprimitive classification questions, do not replace the geometric/group-action constraints by a simpler ad hoc proxy such as a single regularity flag.
- **Quadratic forms over local fields**: split by residue characteristic 2 vs odd and run Witt / Springer decomposition respectively.
- **Continuum theory**: distinguish "end point" vs "branch point" strictly from the covering definition, never from intuition.
- **Matrix semigroups**: verify feasibility on 2×2 matrices first, then generalize to n.
- **Combinatorial probability with novel alphabet/structure (e.g. n-base genetic code, derangements over non-standard alphabets)**: when the problem defines a non-standard combinatorial structure and asks for an asymptotic or limit probability, derive the generating function or product formula directly from first principles. For permutation/derangement-type limits over a k-element alphabet of length n, the limit as n→∞ converges to a product ∏ᵢ(1 − 1/kⁱ); verify this by checking the inclusion-exclusion series. The answer must be expressed as a closed-form limit expression matching the problem's notation, not a case-split approximation.

## Python Tool Usage Notes
- When a string literal in Python code contains an apostrophe (`G=G'`, `O'Nan-Scott`, `Schur's lemma`, `G/Z(G)` etc.), **wrap the whole string in double quotes** and do not try to backslash-escape the apostrophe inside a single-quoted string. Example: write `"G=G' (perfect)"`, never `'G=G\' (perfect)'`. The interpreter used by the tool has repeatedly failed with `unterminated string literal` on backslash-escaped apostrophes inside single-quoted strings, wasting whole steps.
- When a string contains **both** single and double quotes, use a triple-double-quoted string (`"""…"""`) instead of escaping. Avoid mixing `'…'` with `\'` — it is the single most common failure mode observed in this skill's traces.
- Prefer ASCII in string literals when the content is for printing/logging only — replace `–`, `—`, `✓`, `✗` with `-`, `--`, `OK`, `X` when used inside Python source; unicode is fine in printed output and JSON data but non-ASCII in source code has also tripped the interpreter.
- If a Python tool call fails with a syntax / parsing error, do **not** retry with the same quoting style — switch quoting strategy (single → double → triple-double, or heredoc via `subprocess`), not the content.

## Output Rules
- Exact match: emit only the normalized result (integer / closed form / "Yes"/"No" / tuple) with no explanation.
- Multiple choice: a single uppercase letter.
- Multi-part problems (a / b / c): answer every part in the order and separator used by the problem.

## Quick Reference
- [resources/patterns.json](resources/patterns.json) — structured record of this skill's target task signatures, failure modes, and source cluster IDs.
