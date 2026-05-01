#!/usr/bin/env python3
"""Markov chain hitting-time and pattern-matching solver.

Usage:
    python markov_solver.py pattern TENETENET --alphabet 26
    python markov_solver.py pattern ABAB --alphabet 4
    python markov_solver.py hitting --transitions "[[0,0.5,0.5],[0,0,1],[0,0,1]]" --absorbing "[2]" --initial 0
    python markov_solver.py escape --transitions "[[0,0.5,0.5],[0.5,0,0.5],[1,0,0]]" --absorbing "[0]" --initial 1
    python markov_solver.py simulate pattern TENETENET --alphabet 26 --runs 50000
"""

from __future__ import annotations

import argparse
import json
import sys
from fractions import Fraction
from typing import Optional


# ---------------------------------------------------------------------------
# Pattern-matching: Conway leading-number algorithm + Markov chain solver
# ---------------------------------------------------------------------------

def conway_expected_time(pattern: str, alphabet_size: int) -> int:
    """Compute E[T] for first occurrence of pattern using Conway's formula.

    E[T] = sum over i=1..n of (alphabet_size^i) if pattern[0:i] == pattern[n-i:n]
    """
    n = len(pattern)
    total = 0
    for i in range(1, n + 1):
        # Check if first i chars equal last i chars
        if pattern[:i] == pattern[n - i:]:
            total += alphabet_size ** i
    return total


def pattern_markov_expected_time(pattern: str, alphabet_size: int) -> Fraction:
    """Build the (n+1)-state Markov chain for pattern matching and solve.

    States 0..n-1 = longest suffix of current output that is a prefix of pattern.
    State n = absorbing (pattern found).
    Returns exact rational expected time.
    """
    n = len(pattern)
    # For each state s (prefix length 0..n-1), on each of alphabet_size symbols:
    # - if symbol matches pattern[s], go to state s+1
    # - else find longest proper suffix of pattern[0:s]+symbol that is a prefix of pattern

    def next_state(s: int, char_idx: int) -> int:
        """Compute next state from state s on character with index char_idx."""
        # Map char_idx to a character in the pattern alphabet if possible
        # Pattern is given as a string; we treat matching chars by index
        # char_idx == 0 means "matches pattern[s]", others are "don't match" (uniform)
        # But we need to track which actual characters appear in pattern
        # For general alphabet: build KMP failure function
        candidate = pattern[:s] + (pattern[s] if char_idx == 0 else chr(0))
        if char_idx == 0:
            return s + 1  # matched
        # For non-matching chars: use KMP to find longest prefix that is a suffix
        # of pattern[0:s] + non_matching
        # Since non_matching != pattern[s], we fall back on suffix of pattern[0:s]
        # that is also a prefix of pattern, of length < s
        k = s
        while k > 0:
            k = _kmp_failure(pattern)[k - 1]
            if char_idx == 0:  # won't happen here
                return k + 1
            # keep trying shorter suffixes
        return 0

    # Build KMP failure function for pattern
    fail = _kmp_failure(pattern)

    def advance(state: int, symbol: str) -> int:
        """KMP advance: given current match length = state, on new symbol."""
        s = state
        while s > 0 and (s >= n or symbol != pattern[s]):
            s = fail[s - 1]
        if symbol == pattern[s]:
            s += 1
        return s

    # Count for each state s, how many of the alphabet_size symbols lead to each next state
    # Pattern characters: treat alphabet as having alphabet_size symbols total
    # For simplicity: assume uniform alphabet where each symbol is equally likely
    # We enumerate which symbols appear in pattern and assign indices
    # For uniform alphabet: probability 1/alphabet_size per symbol

    # Build transition counts: trans[s][t] = number of symbols taking s -> t
    trans_count = [[0] * (n + 1) for _ in range(n)]

    # Characters that appear in the pattern
    pattern_chars = set(pattern)

    # For each state s:
    #   - 1 symbol matches pattern[s] (or 0 if s==n, but s<n here)
    #   - (alphabet_size - 1) symbols don't match
    # But among non-matching, different chars may lead to different states via KMP
    # For exact solution: enumerate all chars in pattern + 1 generic "other"

    for s in range(n):
        # Symbol that matches pattern[s]
        ns = advance(s, pattern[s])
        trans_count[s][ns] += 1

        # For each other pattern character (may lead to specific non-zero state)
        for c in pattern_chars:
            if c == pattern[s]:
                continue
            ns2 = advance(s, c)
            trans_count[s][ns2] += 1

        # Remaining alphabet_size - len(pattern_chars) symbols all lead to 0
        # (since they don't appear in pattern, KMP always falls to 0)
        remaining = alphabet_size - len(pattern_chars)
        if remaining > 0:
            trans_count[s][0] += remaining

    # Verify row sums
    for s in range(n):
        assert sum(trans_count[s]) == alphabet_size, f"Row {s} sum = {sum(trans_count[s])}"

    # Build rational transition matrix Q for non-absorbing states (0..n-1)
    # E[T|s] = 1 + sum_t (trans[s][t]/k) * E[T|t]   for t != n
    # E[T|n] = 0
    # => E[T|s] - sum_{t<n} (trans[s][t]/k) * E[T|t] = 1

    k = Fraction(alphabet_size)
    # Build system: (I - Q) x = 1
    # Q[s][t] = trans_count[s][t] / alphabet_size  for t < n
    size = n
    A = [[Fraction(0)] * size for _ in range(size)]
    b = [Fraction(1)] * size

    for s in range(size):
        A[s][s] = Fraction(1)
        for t in range(size):
            A[s][t] -= Fraction(trans_count[s][t], alphabet_size)

    x = _solve_rational(A, b, size)
    return x[0]  # expected time from state 0 (empty prefix)


def _kmp_failure(pattern: str) -> list[int]:
    """KMP failure function."""
    n = len(pattern)
    fail = [0] * n
    k = 0
    for i in range(1, n):
        while k > 0 and pattern[k] != pattern[i]:
            k = fail[k - 1]
        if pattern[k] == pattern[i]:
            k += 1
        fail[i] = k
    return fail


def _solve_rational(A: list, b: list, n: int) -> list:
    """Gaussian elimination with exact rational arithmetic."""
    # Augmented matrix
    mat = [row[:] + [b[i]] for i, row in enumerate(A)]

    for col in range(n):
        # Find pivot
        pivot = None
        for row in range(col, n):
            if mat[row][col] != 0:
                pivot = row
                break
        if pivot is None:
            raise ValueError(f"Singular matrix at column {col}")
        mat[col], mat[pivot] = mat[pivot], mat[col]
        # Eliminate
        for row in range(n):
            if row == col:
                continue
            if mat[row][col] == 0:
                continue
            factor = mat[row][col] / mat[col][col]
            for j in range(n + 1):
                mat[row][j] -= factor * mat[col][j]

    return [mat[i][n] / mat[i][i] for i in range(n)]


# ---------------------------------------------------------------------------
# General hitting-time solver from transition matrix
# ---------------------------------------------------------------------------

def hitting_time_solver(
    transitions: list[list[float]],
    absorbing: list[int],
    initial: int,
) -> float:
    """Compute expected hitting time to any absorbing state from initial state.

    transitions: n x n row-stochastic matrix (lists of floats)
    absorbing: list of absorbing state indices
    initial: starting state index
    """
    import numpy as np

    T = np.array(transitions, dtype=float)
    n = T.shape[0]
    absorbing_set = set(absorbing)
    transient = [s for s in range(n) if s not in absorbing_set]

    # Verify rows sum to ~1
    for i, row in enumerate(T):
        assert abs(sum(row) - 1.0) < 1e-9, f"Row {i} does not sum to 1: {sum(row)}"

    # Sub-matrix Q for transient states
    idx = {s: i for i, s in enumerate(transient)}
    m = len(transient)
    Q = np.zeros((m, m))
    for i, s in enumerate(transient):
        for j, t in enumerate(transient):
            Q[i][j] = T[s][t]

    # (I - Q) h = 1
    A = np.eye(m) - Q
    b = np.ones(m)
    h = np.linalg.solve(A, b)

    if initial in absorbing_set:
        return 0.0
    return float(h[idx[initial]])


def escape_probability_solver(
    transitions: list[list[float]],
    target: list[int],
    absorbing: list[int],
    initial: int,
) -> float:
    """Compute probability of being absorbed in target states before others.

    target: subset of absorbing states (the "good" absorbing states)
    absorbing: all absorbing states
    """
    import numpy as np

    T = np.array(transitions, dtype=float)
    n = T.shape[0]
    absorbing_set = set(absorbing)
    target_set = set(target)
    transient = [s for s in range(n) if s not in absorbing_set]

    idx = {s: i for i, s in enumerate(transient)}
    m = len(transient)

    # p[s] = probability of reaching target from transient state s
    # p[s] = sum_t T[s,t]*p[t] + sum_{t in target} T[s,t]
    Q = np.zeros((m, m))
    r = np.zeros(m)
    for i, s in enumerate(transient):
        for j, t in enumerate(transient):
            Q[i][j] = T[s][t]
        for t in target_set:
            r[i] += T[s][t]

    A = np.eye(m) - Q
    p = np.linalg.solve(A, r)

    if initial in target_set:
        return 1.0
    if initial in absorbing_set:
        return 0.0
    return float(p[idx[initial]])


# ---------------------------------------------------------------------------
# Monte Carlo simulation (for verification)
# ---------------------------------------------------------------------------

def simulate_pattern(pattern: str, alphabet_size: int, runs: int = 100000) -> float:
    """Estimate E[T] by Monte Carlo simulation."""
    import random
    total = 0
    chars = list(range(alphabet_size))
    fail = _kmp_failure(pattern)
    for _ in range(runs):
        state = 0
        steps = 0
        n = len(pattern)
        while state < n:
            steps += 1
            c_idx = random.randint(0, alphabet_size - 1)
            # Map c_idx to a char: 0..len(pattern_chars)-1 map to pattern chars, rest are "other"
            # For uniform simulation: just use integers as chars
            c = chr(c_idx + 65) if c_idx < 26 else chr(c_idx)
            # KMP advance with integer symbol
            # Re-implement with numeric symbols
            while state > 0 and (state >= n or c_idx != ord(pattern[state]) - ord(pattern[0]) + c_idx):
                # Use string-based KMP on the actual pattern
                break
            # Simpler: rebuild using actual pattern matching
            # treat symbol as: if c_idx == position_in_alphabet_for_pattern[state], match
            # For simplicity, use the string approach: generate actual chars
            break
        total += steps
    # Simpler Monte Carlo with string generation
    total = 0
    alphabet = [chr(65 + i % 26) + str(i // 26) if alphabet_size > 26 else chr(65 + i)
                for i in range(alphabet_size)]
    # Just use integers for speed
    import random
    for _ in range(runs):
        state = 0
        steps = 0
        n = len(pattern)
        pattern_vals = [ord(c) for c in pattern]
        while state < n:
            steps += 1
            sym = random.randint(0, alphabet_size - 1)
            while state > 0 and (state >= n or sym != pattern_vals[state]):
                state = fail[state - 1]
            if state < n and sym == pattern_vals[state]:
                state += 1
        total += steps
    return total / runs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Markov chain hitting-time solver")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # pattern subcommand
    p_pat = sub.add_parser("pattern", help="Expected time until a pattern appears")
    p_pat.add_argument("pattern", type=str, help="Target pattern string")
    p_pat.add_argument("--alphabet", type=int, default=26, help="Alphabet size (default 26)")
    p_pat.add_argument("--method", choices=["conway", "markov", "both"], default="both")

    # hitting subcommand
    p_hit = sub.add_parser("hitting", help="Expected hitting time from transition matrix")
    p_hit.add_argument("--transitions", type=str, required=True, help="JSON n x n matrix")
    p_hit.add_argument("--absorbing", type=str, required=True, help="JSON list of absorbing states")
    p_hit.add_argument("--initial", type=int, required=True, help="Initial state index")

    # escape subcommand
    p_esc = sub.add_parser("escape", help="Escape probability from transition matrix")
    p_esc.add_argument("--transitions", type=str, required=True, help="JSON n x n matrix")
    p_esc.add_argument("--target", type=str, required=True, help="JSON list of target absorbing states")
    p_esc.add_argument("--absorbing", type=str, required=True, help="JSON list of all absorbing states")
    p_esc.add_argument("--initial", type=int, required=True, help="Initial state index")

    # simulate subcommand
    p_sim = sub.add_parser("simulate", help="Monte Carlo verification")
    p_sim.add_argument("type", choices=["pattern"], help="Simulation type")
    p_sim.add_argument("pattern", type=str, help="Target pattern string")
    p_sim.add_argument("--alphabet", type=int, default=26)
    p_sim.add_argument("--runs", type=int, default=100000)

    args = parser.parse_args()

    if args.cmd == "pattern":
        pat = args.pattern
        k = args.alphabet

        if args.method in ("conway", "both"):
            conway = conway_expected_time(pat, k)
            print(f"Conway formula:  E[T] = {conway}")

        if args.method in ("markov", "both"):
            exact = pattern_markov_expected_time(pat, k)
            print(f"Markov chain:    E[T] = {exact} = {float(exact):.2f}")

        if args.method == "both":
            conway = conway_expected_time(pat, k)
            if exact == conway:
                print("✓ Both methods agree.")
            else:
                print(f"✗ Mismatch: Conway={conway}, Markov={exact}")

    elif args.cmd == "hitting":
        T = json.loads(args.transitions)
        absorbing = json.loads(args.absorbing)
        result = hitting_time_solver(T, absorbing, args.initial)
        print(f"E[hitting time from state {args.initial}] = {result:.6f}")

    elif args.cmd == "escape":
        T = json.loads(args.transitions)
        target = json.loads(args.target)
        absorbing = json.loads(args.absorbing)
        result = escape_probability_solver(T, target, absorbing, args.initial)
        print(f"P[escape to target from state {args.initial}] = {result:.6f}")

    elif args.cmd == "simulate":
        est = simulate_pattern(args.pattern, args.alphabet, args.runs)
        exact = conway_expected_time(args.pattern, args.alphabet)
        print(f"Monte Carlo ({args.runs} runs): E[T] ≈ {est:.1f}")
        print(f"Conway exact:                  E[T] = {exact}")
        print(f"Relative error: {abs(est - exact) / exact * 100:.2f}%")


if __name__ == "__main__":
    main()
