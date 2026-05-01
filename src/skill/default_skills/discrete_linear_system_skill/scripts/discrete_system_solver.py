#!/usr/bin/env python3
"""Discrete-time linear system solver: difference equations, observer/controller design, canonical forms.

Usage:
    python discrete_system_solver.py diffeq --coeffs "8,-6,1" --rhs "constant:1" --init "y0=1,ym1=2"
    python discrete_system_solver.py observer --A "[[1,1,0],[2,1,1],[0,2,0]]" --C "[[0,1,0],[1,1,0]]" --poles "[0,0,0]"
    python discrete_system_solver.py deadbeat --A "[[1,1,0],[2,1,1],[0,2,0]]" --C "[[0,1,0],[1,1,0]]"
    python discrete_system_solver.py canonical --A "[[1,1,0],[2,1,1],[0,2,0]]" --B "[[0],[1],[-1]]" --C "[[0,1,0],[1,1,0]]" --form observer
    python discrete_system_solver.py stability --A "[[-1,0,0,1],[1,0,0,2],[0,1,0,-1],[-1,0,1,-1]]"
    python discrete_system_solver.py charpoly --A "[[1,1,0],[2,1,1],[0,2,0]]"
    python discrete_system_solver.py verify --diffeq "8,-6,1" --solution "A,0.5,B,0.25,0.333" --init "y0=1,ym1=2" --steps 5
"""

from __future__ import annotations

import argparse
import json
import sys
from fractions import Fraction
from typing import Optional


# ---------------------------------------------------------------------------
# Difference equation solver
# ---------------------------------------------------------------------------

def solve_diffeq(
    coeffs: list[float],
    rhs_type: str,
    rhs_param: float,
    y0: float,
    ym1: Optional[float] = None,
    n_steps: int = 10,
) -> dict:
    """Solve a linear constant-coefficient difference equation.

    Equation: coeffs[0]*y[n] + coeffs[1]*y[n-1] + ... = rhs(n)

    Returns:
        {
          'char_roots': [...],
          'homogeneous_form': str,
          'particular_solution': str,
          'general_solution': str,
          'constants': dict,
          'values': [y[0], y[1], ..., y[n_steps-1]]
        }
    """
    import numpy as np
    import sympy as sp

    order = len(coeffs) - 1
    a = [Fraction(c).limit_denominator(10000) for c in coeffs]

    # Characteristic polynomial: a[0]*r^order + a[1]*r^(order-1) + ... + a[order] = 0
    char_poly_coeffs = [float(x) for x in a]
    roots_np = np.roots(char_poly_coeffs)

    # Use sympy for exact roots when possible
    r = sp.Symbol('r')
    char_poly = sum(float(a[i]) * r ** (order - i) for i in range(order + 1))
    try:
        roots_sym = sp.solve(char_poly, r)
    except Exception:
        roots_sym = roots_np.tolist()

    print(f"Characteristic polynomial: {char_poly}")
    print(f"Characteristic roots: {roots_sym}")

    # Particular solution
    part_sol_expr = None
    if rhs_type == "constant":
        # Try y_p = K
        # a[0]*K + a[1]*K + ... + a[order]*K = rhs_param
        s = sum(float(x) for x in a)
        if abs(s) > 1e-10:
            K = rhs_param / s
            part_sol_expr = f"y_p[n] = {K:.6g} (constant)"
            print(f"Particular solution: {part_sol_expr}")
        else:
            # 1 is a root, try y_p = A*n
            # Substitute and solve for A
            print("Sum of coefficients = 0 (1 is a root). Trying y_p = A*n")
            # For simple cases: just note it
            part_sol_expr = "y_p[n] = A*n (1 is characteristic root, need manual derivation)"
    elif rhs_type.startswith("exp:"):
        alpha = float(rhs_type.split(":")[1])
        # Try y_p = A * alpha^n
        val = sum(float(a[i]) * alpha ** (order - i) for i in range(order + 1))
        if abs(val) > 1e-10:
            A = rhs_param / val
            part_sol_expr = f"y_p[n] = {A:.6g} * {alpha}^n"
        else:
            part_sol_expr = f"alpha={alpha} is a characteristic root; try y_p = A*n*{alpha}^n"
        print(f"Particular solution: {part_sol_expr}")

    # Compute actual values by direct iteration
    # Use a dict for negative indices: y_dict[k] = y[k]
    y_dict: dict[int, float] = {}
    if y0 is not None:
        y_dict[0] = y0
    if ym1 is not None:
        y_dict[-1] = ym1

    # Iterate forward from n=1 upward whenever all past values are available
    for n_val in range(1, n_steps):
        # Check if all past values available
        needed = list(range(n_val - order, n_val))
        if all(k in y_dict for k in needed):
            rhs_val = rhs_param if rhs_type == "constant" else 0.0
            total = rhs_val
            for k in range(1, order + 1):
                total -= float(a[k]) * y_dict[n_val - k]
            y_dict[n_val] = total / float(a[0])

    values = [y_dict.get(i, float("nan")) for i in range(n_steps)]

    return {
        "char_roots": [str(x) for x in roots_sym],
        "particular_solution": part_sol_expr,
        "values": [round(v, 8) for v in values],
    }


# ---------------------------------------------------------------------------
# Observer / controller design
# ---------------------------------------------------------------------------

def design_observer(
    A: list[list[float]],
    C: list[list[float]],
    poles: list[float],
) -> list[list[float]]:
    """Design observer gain L such that eig(A - L*C) = poles.

    Uses scipy.signal.place_poles via the dual (controller) system.
    Returns L as a list of lists.
    """
    import numpy as np
    from scipy.signal import place_poles

    A_np = np.array(A, dtype=float)
    C_np = np.array(C, dtype=float)
    p = np.array(poles, dtype=complex)

    # Observer design: place_poles on (A^T, C^T) then transpose
    result = place_poles(A_np.T, C_np.T, p)
    L = result.gain_matrix.T

    # Verify
    eigvals = np.linalg.eigvals(A_np - L @ C_np)
    print(f"Desired poles:  {sorted(poles)}")
    print(f"Achieved poles: {sorted(eigvals.real.tolist())}")

    return L.tolist()


def design_deadbeat_observer(
    A: list[list[float]],
    C: list[list[float]],
) -> tuple[list[list[float]], int]:
    """Design deadbeat observer (all poles at 0) and find minimum steps.

    Returns (L, min_steps) where min_steps = observability index.
    """
    import numpy as np

    A_np = np.array(A, dtype=float)
    C_np = np.array(C, dtype=float)
    n = A_np.shape[0]

    # Observability index = smallest k with rank(O_k) = n
    O = C_np.copy()
    obs_index = None
    for k in range(1, n + 1):
        rank = np.linalg.matrix_rank(O)
        if rank == n:
            obs_index = k
            break
        if k < n:
            O = np.vstack([O, C_np @ np.linalg.matrix_power(A_np, k)])

    if obs_index is None:
        print("WARNING: System is not observable — deadbeat observer impossible.")
        obs_index = n

    print(f"Observability rank = n={n} achieved at step k={obs_index}")
    print(f"Minimum steps for zero error: {obs_index}")

    # Place all poles at 0
    # scipy.signal.place_poles requires poles repeated at most rank(C) times for MIMO
    # Use tiny distinct offsets for numerical stability, then print rounded result
    p_rank = int(np.linalg.matrix_rank(C_np))
    if p_rank < n:
        # Use distinct small values near 0
        eps = 1e-6
        poles = [eps * (i + 1) * (-1) ** i for i in range(n)]
        print(f"Note: C rank={p_rank} < n={n}; using near-zero poles {poles[:4]}… for place_poles")
    else:
        poles = [0.0] * n
    L = design_observer(A, C, poles)
    # Verify achieved poles are near 0
    A_np2 = np.array(A, dtype=float)
    C_np2 = np.array(C, dtype=float)
    L_np = np.array(L, dtype=float)
    achieved = np.linalg.eigvals(A_np2 - L_np @ C_np2)
    print(f"Achieved eigenvalues of (A-LC): {np.round(achieved, 6).tolist()}")
    return L, obs_index


def check_stability(A: list[list[float]]) -> dict:
    """Check stability of discrete-time system x[n+1] = A x[n]."""
    import numpy as np

    A_np = np.array(A, dtype=float)
    eigvals = np.linalg.eigvals(A_np)
    magnitudes = np.abs(eigvals)
    stable = bool(np.all(magnitudes < 1.0 - 1e-10))
    marginally = bool(np.all(magnitudes <= 1.0 + 1e-10))

    print(f"Eigenvalues: {eigvals}")
    print(f"Magnitudes:  {magnitudes}")
    print(f"Stable (all |λ| < 1): {stable}")
    print(f"Marginally stable (all |λ| <= 1): {marginally}")

    return {
        "eigenvalues": eigvals.tolist(),
        "magnitudes": magnitudes.tolist(),
        "stable": stable,
        "marginally_stable": marginally,
    }


def characteristic_polynomial(A: list[list[float]]) -> list[float]:
    """Compute characteristic polynomial coefficients of A (highest degree first)."""
    import numpy as np

    A_np = np.array(A, dtype=float)
    coeffs = np.poly(A_np)  # coefficients of det(λI - A)
    print(f"Characteristic polynomial coefficients (highest to lowest degree): {coeffs}")
    print(f"  = λ^{len(coeffs)-1}", end="")
    for i, c in enumerate(coeffs[1:], 1):
        sign = "+" if c >= 0 else "-"
        print(f" {sign} {abs(c):.6g}·λ^{len(coeffs)-1-i}", end="")
    print()
    return coeffs.tolist()


def observer_canonical_form(
    A: list[list[float]],
    B: list[list[float]],
    C: list[list[float]],
) -> dict:
    """Transform (A, B, C) to observer canonical form.

    Returns transformed (A_tilde, B_tilde, C_tilde, T) where T is the transformation.
    """
    import numpy as np

    A_np = np.array(A, dtype=float)
    B_np = np.array(B, dtype=float)
    C_np = np.array(C, dtype=float)
    n = A_np.shape[0]

    # Characteristic polynomial coefficients
    char_coeffs = np.poly(A_np)  # [1, a1, a2, ..., an]
    a = char_coeffs[1:]  # [a1, ..., an] (monic, coefficient of λ^(n-1), ..., λ^0)

    # Observer canonical form matrices
    # A_tilde = companion matrix (observer form)
    A_tilde = np.zeros((n, n))
    for i in range(n - 1):
        A_tilde[i + 1][i] = 1.0
    for i in range(n):
        A_tilde[i][n - 1] = -a[n - 1 - i]

    print(f"Characteristic polynomial coefficients a = {a}")
    print(f"A in observer canonical form:\n{A_tilde}")

    # Observability matrix of original system
    O_orig = np.zeros((n * C_np.shape[0], n))
    for k in range(n):
        O_orig[k * C_np.shape[0]:(k + 1) * C_np.shape[0], :] = C_np @ np.linalg.matrix_power(A_np, k)

    # For SISO (C is 1xn), use standard transformation
    if C_np.shape[0] == 1:
        # Observability matrix O = [C; CA; CA^2; ...]
        O = np.vstack([C_np @ np.linalg.matrix_power(A_np, k) for k in range(n)])
        # Canonical observability matrix O_tilde
        O_tilde = np.eye(n)  # for observer canonical form, O_tilde has specific structure
        # T = O_tilde @ inv(O)
        try:
            T = O_tilde @ np.linalg.inv(O)
            A_check = T @ A_np @ np.linalg.inv(T)
            B_tilde = T @ B_np
            C_tilde = C_np @ np.linalg.inv(T)
            print(f"Transformation T:\n{T}")
            print(f"B_tilde:\n{B_tilde}")
            print(f"C_tilde:\n{C_tilde}")
            print(f"Verification A_tilde = T A T^-1:\n{A_check}")
        except np.linalg.LinAlgError:
            print("WARNING: Observability matrix is singular — system not observable.")
            T = None
            B_tilde = None
            C_tilde = None
    else:
        print("MIMO system: canonical form computation requires manual block structure.")
        T = None
        B_tilde = None
        C_tilde = None

    return {
        "A_tilde": A_tilde.tolist(),
        "B_tilde": B_tilde.tolist() if B_tilde is not None else None,
        "C_tilde": C_tilde.tolist() if C_tilde is not None else None,
        "T": T.tolist() if T is not None else None,
    }


def verify_solution(
    coeffs_str: str,
    solution_str: str,
    init_str: str,
    steps: int = 5,
) -> None:
    """Verify a proposed closed-form solution against direct iteration.

    coeffs_str: "8,-6,1" for 8y[n] - 6y[n-1] + y[n-2]
    solution_str: "A,0.5,B,0.25,0.333" for y[n] = A*(0.5)^n + B*(0.25)^n + 0.333
    init_str: "y0=1,ym1=2"
    """
    import sympy as sp

    coeffs = [float(x) for x in coeffs_str.split(",")]
    parts = solution_str.split(",")
    # Parse solution: alternating constant_name, base
    # Format: "A,0.5,B,0.25,0.333" means y[n] = A*(0.5)^n + B*(0.25)^n + 0.333
    # Last element may be a constant (particular solution)

    # Parse initial conditions
    init = {}
    for item in init_str.split(","):
        k, v = item.split("=")
        init[k.strip()] = float(v)

    y0 = init.get("y0", 0.0)
    ym1 = init.get("ym1", None)

    # Direct iteration
    order = len(coeffs) - 1
    y = [0.0] * (steps + order + 1)
    y[order] = y0
    if order >= 2 and ym1 is not None:
        y[order - 1] = ym1

    rhs = 1.0  # assume constant RHS = 1 for standard test
    for n in range(order, order + steps):
        total = rhs
        for k in range(1, order + 1):
            total -= coeffs[k] * y[n - k]
        y[n] = total / coeffs[0]

    direct_vals = y[order: order + steps]

    print("Verification — direct iteration values:")
    for i, v in enumerate(direct_vals):
        print(f"  y[{i}] = {v:.6f}")

    # Parse the symbolic solution and solve for constants
    # Detect terms: pairs (coeff_name, base) + optional constant
    sym_parts = parts
    terms = []  # (symbol_name, base) or (value,) for constants
    i = 0
    while i < len(sym_parts):
        try:
            float(sym_parts[i])
            # It's a number — either a base after a letter or a standalone constant
            if i > 0:
                try:
                    float(sym_parts[i - 1])
                    # Previous was also a number → standalone constant
                    terms.append(("const", float(sym_parts[i])))
                except ValueError:
                    # Previous was a letter → this is the base
                    terms[-1] = (terms[-1][0], float(sym_parts[i]))
            else:
                terms.append(("const", float(sym_parts[i])))
        except ValueError:
            # It's a variable name
            terms.append((sym_parts[i], None))
        i += 1

    # Build sympy expressions
    sym_vars = {name: sp.Symbol(name) for name, base in terms if name != "const"}
    n_sym = sp.Symbol("n")
    expr = sp.Integer(0)
    for name, base in terms:
        if name == "const":
            expr += sp.Rational(base).limit_denominator(10000)
        elif base is not None:
            expr += sym_vars[name] * sp.Rational(base).limit_denominator(10000) ** n_sym

    print(f"\nSymbolic solution: y[n] = {expr}")

    # Solve for constants using initial values
    if len(sym_vars) >= 1:
        eqs = []
        ns = [0] if order == 1 else [0, -1]
        for n_val in ns[:len(sym_vars)]:
            val = direct_vals[n_val] if n_val >= 0 else ym1
            eq = expr.subs(n_sym, n_val) - val
            eqs.append(eq)
        try:
            sol = sp.solve(eqs, list(sym_vars.values()))
            print(f"Constants: {sol}")
            expr_solved = expr.subs(sol)
            print(f"Closed form: y[n] = {expr_solved}")
            # Verify
            print("\nComparison:")
            for i in range(steps):
                sym_val = float(expr_solved.subs(n_sym, i))
                print(f"  y[{i}]: direct={direct_vals[i]:.6f}, formula={sym_val:.6f}, match={abs(sym_val - direct_vals[i]) < 1e-4}")
        except Exception as e:
            print(f"Could not solve for constants: {e}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_matrix(s: str) -> list[list[float]]:
    """Parse JSON matrix string."""
    return json.loads(s)


def main():
    parser = argparse.ArgumentParser(description="Discrete linear system solver")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # diffeq subcommand
    p_de = sub.add_parser("diffeq", help="Solve difference equation")
    p_de.add_argument("--coeffs", required=True, help="Coefficients a0,a1,...,am (comma-separated)")
    p_de.add_argument("--rhs", required=True, help="RHS type: 'constant:K' or 'exp:alpha'")
    p_de.add_argument("--init", required=True, help="Initial conditions: y0=1,ym1=2")
    p_de.add_argument("--steps", type=int, default=10, help="Number of values to compute")

    # observer subcommand
    p_obs = sub.add_parser("observer", help="Design observer with specified poles")
    p_obs.add_argument("--A", required=True, help="JSON state matrix A")
    p_obs.add_argument("--C", required=True, help="JSON output matrix C")
    p_obs.add_argument("--poles", required=True, help="JSON list of desired poles")

    # deadbeat subcommand
    p_db = sub.add_parser("deadbeat", help="Design deadbeat observer (all poles at 0)")
    p_db.add_argument("--A", required=True, help="JSON state matrix A")
    p_db.add_argument("--C", required=True, help="JSON output matrix C")

    # canonical subcommand
    p_can = sub.add_parser("canonical", help="Reduce to canonical form")
    p_can.add_argument("--A", required=True, help="JSON state matrix A")
    p_can.add_argument("--B", required=True, help="JSON input matrix B")
    p_can.add_argument("--C", required=True, help="JSON output matrix C")
    p_can.add_argument("--form", choices=["observer", "controller"], default="observer")

    # stability subcommand
    p_stab = sub.add_parser("stability", help="Check stability of A")
    p_stab.add_argument("--A", required=True, help="JSON state matrix A")

    # charpoly subcommand
    p_cp = sub.add_parser("charpoly", help="Compute characteristic polynomial")
    p_cp.add_argument("--A", required=True, help="JSON state matrix A")

    # verify subcommand
    p_ver = sub.add_parser("verify", help="Verify a proposed closed-form solution")
    p_ver.add_argument("--diffeq", required=True, help="Equation coefficients a0,a1,...,am")
    p_ver.add_argument("--solution", required=True, help="Solution form: A,base1,B,base2,const")
    p_ver.add_argument("--init", required=True, help="Initial conditions: y0=1,ym1=2")
    p_ver.add_argument("--steps", type=int, default=5)

    args = parser.parse_args()

    if args.cmd == "diffeq":
        coeffs = [float(x) for x in args.coeffs.split(",")]
        rhs_parts = args.rhs.split(":")
        rhs_type = rhs_parts[0]
        rhs_param = float(rhs_parts[1]) if len(rhs_parts) > 1 else 0.0

        init = {}
        for item in args.init.split(","):
            k, v = item.split("=")
            init[k.strip()] = float(v)

        result = solve_diffeq(
            coeffs,
            rhs_type=rhs_type,
            rhs_param=rhs_param,
            y0=init.get("y0", 0.0),
            ym1=init.get("ym1"),
            n_steps=args.steps,
        )
        print(f"\nValues y[0]..y[{args.steps-1}]: {result['values']}")

    elif args.cmd == "observer":
        A = parse_matrix(args.A)
        C = parse_matrix(args.C)
        poles = json.loads(args.poles)
        L = design_observer(A, C, poles)
        print(f"\nObserver gain L:\n{json.dumps(L, indent=2)}")

    elif args.cmd == "deadbeat":
        A = parse_matrix(args.A)
        C = parse_matrix(args.C)
        L, steps = design_deadbeat_observer(A, C)
        print(f"\nDeadbeat observer gain L (min steps={steps}):\n{json.dumps(L, indent=2)}")

    elif args.cmd == "canonical":
        A = parse_matrix(args.A)
        B = parse_matrix(args.B)
        C = parse_matrix(args.C)
        result = observer_canonical_form(A, B, C)
        print(f"\nResult: {json.dumps({k: v for k, v in result.items() if v is not None}, indent=2)}")

    elif args.cmd == "stability":
        A = parse_matrix(args.A)
        check_stability(A)

    elif args.cmd == "charpoly":
        A = parse_matrix(args.A)
        characteristic_polynomial(A)

    elif args.cmd == "verify":
        verify_solution(args.diffeq, args.solution, args.init, args.steps)


if __name__ == "__main__":
    main()
