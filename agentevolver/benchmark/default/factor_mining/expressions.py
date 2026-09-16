"""Bounded, causal expression interpreter for the factor research benchmark."""

from __future__ import annotations

import ast
import hashlib
import math
import operator
from dataclasses import dataclass

import numpy as np
import pandas as pd

MAX_LOOKBACK = 256
MAX_NODES = 128


def _zscore(x):
    return x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1).replace(0, np.nan), axis=0)


def _neutralize(x, exposure):
    mask = x.notna() & exposure.notna()
    a, b = x.where(mask), exposure.where(mask)
    a, b = a.sub(a.mean(axis=1), axis=0), b.sub(b.mean(axis=1), axis=0)
    beta = (a * b).sum(axis=1).div((b * b).sum(axis=1).replace(0, np.nan))
    return a - b.mul(beta, axis=0)


# name -> (category, argument names, implementation, window argument index or None)
OPERATORS = {
    "ts_mean": ("time_series", "x, window", lambda x, w: x.rolling(w).mean(), 1),
    "ts_std": ("time_series", "x, window", lambda x, w: x.rolling(w).std(), 1),
    "ts_min": ("time_series", "x, window", lambda x, w: x.rolling(w).min(), 1),
    "ts_max": ("time_series", "x, window", lambda x, w: x.rolling(w).max(), 1),
    "ts_sum": ("time_series", "x, window", lambda x, w: x.rolling(w).sum(), 1),
    "ts_rank": ("time_series", "x, window", lambda x, w: x.rolling(w).rank(pct=True), 1),
    "ts_corr": ("time_series", "x, y, window", lambda x, y, w: x.rolling(w).corr(y), 2),
    "delay": ("time_series", "x, window", lambda x, w: x.shift(w), 1),
    "delta": ("time_series", "x, window", lambda x, w: x.diff(w), 1),
    "rank": ("cross_section", "x", lambda x: x.rank(axis=1, pct=True), None),
    "zscore": ("cross_section", "x", _zscore, None),
    "demean": ("cross_section", "x", lambda x: x.sub(x.mean(axis=1), axis=0), None),
    "neutralize": ("cross_section", "x, exposure", _neutralize, None),
    "winsorize": ("cross_section", "x, quantile",
                  lambda x, q: x.clip(lower=x.quantile(q, axis=1),
                                      upper=x.quantile(1 - q, axis=1), axis=0), None),
    "abs": ("elementwise", "x", abs, None),
    "sign": ("elementwise", "x", np.sign, None),
    "log": ("elementwise", "x", lambda x: np.log(x.where(x > 0)), None),
    "sqrt": ("elementwise", "x", lambda x: np.sqrt(x.where(x >= 0)), None),
    "minimum": ("elementwise", "x, y", np.minimum, None),
    "maximum": ("elementwise", "x, y", np.maximum, None),
    "if_else": ("elementwise", "condition, yes, no",
                lambda c, y, n: pd.DataFrame(np.where(c, y, n), index=c.index,
                                             columns=c.columns).where(c.notna()), None),
}
BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.Pow: operator.pow}
COMPARE = {ast.Gt: operator.gt, ast.Lt: operator.lt, ast.GtE: operator.ge,
           ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne}


@dataclass(frozen=True)
class Expression:
    text: str
    tree: ast.Expression
    names: frozenset[str]
    lookback: int
    structure: str
    paradigm: str

    @classmethod
    def parse(cls, text: str, fields) -> "Expression":
        if not isinstance(text, str) or not 0 < len(text) <= 2048:
            raise ValueError("expression must contain 1..2048 characters")
        try:
            tree = ast.parse(text.strip(), mode="eval")
        except (SyntaxError, RecursionError) as exc:
            raise ValueError("invalid expression syntax") from exc
        if len(list(ast.walk(tree))) > MAX_NODES:
            raise ValueError("expression exceeds node budget")
        names, categories = set(), set()

        def visit(node, depth=0):
            if depth > 20:
                raise ValueError("expression too deeply nested")
            if isinstance(node, ast.Constant):
                if type(node.value) not in (int, float) or not math.isfinite(node.value):
                    raise ValueError("only finite numeric constants are permitted")
                if abs(node.value) > 1_000_000:
                    raise ValueError("constant too large")
                return 0, f"number:{node.value}"
            if isinstance(node, ast.Name) and node.id in fields:
                names.add(node.id)
                return 0, node.id
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                lag, key = visit(node.operand, depth + 1)
                return lag, f"{type(node.op).__name__}({key})"
            if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
                if isinstance(node.op, ast.Pow) and not (
                    isinstance(node.right, ast.Constant) and type(node.right.value) in (int, float)
                    and abs(node.right.value) <= 8
                ):
                    raise ValueError("power needs a literal exponent between -8 and 8")
                a, x = visit(node.left, depth + 1)
                b, y = visit(node.right, depth + 1)
                keys = sorted([x, y]) if isinstance(node.op, (ast.Add, ast.Mult)) else [x, y]
                return max(a, b), f"{type(node.op).__name__}({','.join(keys)})"
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARE:
                a, x = visit(node.left, depth + 1)
                b, y = visit(node.comparators[0], depth + 1)
                return max(a, b), f"{type(node.ops[0]).__name__}({x},{y})"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                name = node.func.id
                if name not in OPERATORS or node.keywords:
                    raise ValueError("unknown operator or keyword argument")
                category, args, _, window = OPERATORS[name]
                if len(node.args) != len(args.split(",")):
                    raise ValueError(f"{name} expects ({args})")
                categories.add(category)
                parts = [visit(x, depth + 1) for x in node.args]
                lag = max(x[0] for x in parts)
                keys = [x[1] for x in parts]
                if window is not None:
                    w = node.args[window]
                    if not isinstance(w, ast.Constant) or type(w.value) is not int or not 1 <= w.value <= MAX_LOOKBACK:
                        raise ValueError("window must be a positive integer literal <= 256; future shifts forbidden")
                    lag += w.value
                    keys[window] = "WINDOW"  # same tree/other period is a variant
                if name == "winsorize":
                    q = node.args[1]
                    if not isinstance(q, ast.Constant) or not 0 <= q.value < 0.5:
                        raise ValueError("winsorize quantile must be a literal in [0, .5)")
                if lag > MAX_LOOKBACK:
                    raise ValueError("combined lookback exceeds 256 bars")
                return lag, f"{name}({','.join(keys)})"
            raise ValueError(f"forbidden expression node: {type(node).__name__}")

        lag, structure = visit(tree.body)
        if not names:
            raise ValueError("a factor must depend on an allowed field")
        return cls(text.strip(), tree, frozenset(names), lag,
                   hashlib.sha256(structure.encode()).hexdigest(),
                   "cross_section" if "cross_section" in categories else "time_series")

    def evaluate(self, fields: dict[str, pd.DataFrame]) -> pd.DataFrame:
        def walk(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                return fields[node.id]
            if isinstance(node, ast.UnaryOp):
                return -walk(node.operand) if isinstance(node.op, ast.USub) else walk(node.operand)
            if isinstance(node, ast.BinOp):
                return BINOPS[type(node.op)](walk(node.left), walk(node.right))
            if isinstance(node, ast.Compare):
                left, right = walk(node.left), walk(node.comparators[0])
                result = COMPARE[type(node.ops[0])](left, right)
                return result.where(pd.notna(left) & pd.notna(right))
            return OPERATORS[node.func.id][2](*[walk(x) for x in node.args])

        try:
            with np.errstate(all="ignore"):
                result = walk(self.tree.body)
        except (TypeError, AttributeError, ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"invalid operator argument types: {exc}") from exc
        reference = next(iter(fields.values()))
        if not isinstance(result, pd.DataFrame) or not result.index.equals(reference.index) or not result.columns.equals(reference.columns):
            raise ValueError("expression must produce a panel of the original shape")
        return result.astype(float).replace([np.inf, -np.inf], np.nan)


def operator_catalog() -> dict:
    return {name: {"category": spec[0], "arguments": spec[1]}
            for name, spec in OPERATORS.items()}
