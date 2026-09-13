"""Validate factor expressions and generate portable DataFrame-to-DataFrame Python code."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import keyword
import math
from pathlib import Path


def load_runtime(path):
    spec = importlib.util.spec_from_file_location("_factor_ops", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNTIME_PATH = Path(__file__).with_name("factor_ops.py")
ops = load_runtime(RUNTIME_PATH)
ALIASES = {"ts_delay": "delay", "ts_delta": "delta", "ts_std_dev": "ts_stddev", "ts_arg_min": "ts_argmin"}
BINARY = {ast.Add: "add", ast.Sub: "subtract", ast.Mult: "multiply", ast.Div: "divide", ast.Pow: "power"}
COMPARE = {ast.Lt: "lt", ast.LtE: "le", ast.Gt: "gt", ast.GtE: "ge", ast.Eq: "eq", ast.NotEq: "ne"}


def expression_ir(expression, fields):
    """Parse a closed expression grammar; never eval agent-provided Python."""
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 16000:
        raise ValueError("Expected a nonempty expression of at most 16000 characters")
    try:
        root = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as error:
        raise ValueError(f"Invalid factor expression: {error}") from error
    if sum(1 for _ in ast.walk(root)) > 512:
        raise ValueError("Expression exceeds 512 syntax nodes; split the hypothesis")

    def operator(name, args, kwargs, depth):
        if name not in ops.OPERATORS:
            if name in ("rank", "zscore", "normalize", "quantile"):
                raise ValueError(f"{name} over the full time series can leak future data; use ts_{name} with a past window")
            raise ValueError(f"Unknown operator: {name}; extend and verify the versioned runtime first")
        name = ALIASES.get(name, name)
        params = ops.OPERATORS[name]["parameters"]
        if len(args) > len(params):
            raise ValueError(f"Too many arguments for {name}")
        values = dict(zip([p[0] for p in params], args))
        for kw in kwargs:
            if kw.arg not in dict(params) or kw.arg in values:
                raise ValueError(f"Unknown/duplicate argument for {name}: {kw.arg}")
            values[kw.arg] = kw.value
        if set(values) != {p[0] for p in params}:
            raise ValueError(f"Expected {name}({', '.join(p[0] for p in params)})")
        children = []
        for key, kind in params:
            value = values[key]
            if kind in ("value", "series"):
                child = parse(value, depth + 1)
                if kind == "series" and not child["fields"]:
                    raise ValueError(f"{name}.{key} needs an observed series")
            else:
                try:
                    literal = ast.literal_eval(value)
                except (ValueError, TypeError, SyntaxError):
                    raise ValueError(f"{name}.{key} must be a literal parameter") from None
                if kind in ("window", "lag", "k"):
                    if type(literal) is not int or literal < (0 if kind == "lag" else 1):
                        raise ValueError(f"Invalid {name}.{key}; future shifts and noninteger windows are forbidden")
                elif (type(literal) not in (int, float) or not math.isfinite(literal)
                      or (not 0 <= literal <= 1 if kind == "fraction" else literal <= 0)):
                    raise ValueError(f"Invalid {name}.{key}")
                child = {"literal": literal, "fields": set(), "lookback": 0}
            children.append(child)
        if name == "kth_element" and children[2]["literal"] > children[1]["literal"]:
            raise ValueError("k cannot exceed the window")
        lookbacks = [c["lookback"] for c in children]
        lookback = None if None in lookbacks else max(lookbacks, default=0)
        if name in ("hump", "days_from_last_change"):
            lookback = None
        elif lookback is not None:
            if name in ("delay", "delta"):
                lookback += children[1]["literal"]
            elif "window" in dict(params).values():
                i = [p[1] for p in params].index("window")
                lookback += children[i]["literal"] - 1
        return {"op": name, "args": children, "fields": set().union(*(c["fields"] for c in children)), "lookback": lookback}

    def parse(node, depth=0):
        if depth > 64:
            raise ValueError("Expression nesting exceeds 64 levels")
        if isinstance(node, ast.Name) and node.id in fields:
            return {"field": node.id, "fields": {node.id}, "lookback": 0}
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if not math.isfinite(node.value):
                raise ValueError("Nonfinite literal")
            return {"literal": node.value, "fields": set(), "lookback": 0}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return operator("reverse", [node.operand], [], depth) if isinstance(node.op, ast.USub) else parse(node.operand, depth + 1)
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            return operator(BINARY[type(node.op)], [node.left, node.right], [], depth)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARE:
            return operator(COMPARE[type(node.ops[0])], [node.left, node.comparators[0]], [], depth)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return operator(node.func.id, node.args, node.keywords, depth)
        raise ValueError(f"Unsupported expression node: {ast.dump(node, include_attributes=False)}")

    ir = parse(root.body)
    if not ir["fields"]:
        raise ValueError("A factor expression must reference an observed input field")
    return ir


def emit(ir, *, python=False):
    if "field" in ir:
        return f"_ops.field(data, {ir['field']!r})" if python else ir["field"]
    if "literal" in ir:
        return repr(ir["literal"])
    args = ", ".join(emit(c, python=python) for c in ir["args"])
    return f"_ops.apply({ir['op']!r}, {args})" if python else f"{ir['op']}({args})"


def prepare(expressions, fields=ops.DEFAULT_FIELDS):
    if (not isinstance(expressions, dict) or not expressions
            or any(not isinstance(k, str) or not k.strip() for k in expressions)):
        raise ValueError("factors must be a nonempty mapping of versioned names to expressions")
    if (not isinstance(fields, (list, tuple)) or not fields or len(set(fields)) != len(fields)
            or any(not isinstance(f, str) or not f.isidentifier() or keyword.iskeyword(f)
                   or f.startswith("_") or f in ops.OPERATORS for f in fields)):
        raise ValueError("fields must be unique public identifiers, separate from operator names")
    trees = {name: expression_ir(expression, fields) for name, expression in expressions.items()}
    metadata = {"schema": 1, "operator_version": ops.VERSION,
                "runtime_sha256": hashlib.sha256(RUNTIME_PATH.read_bytes()).hexdigest(),
                "required_fields": sorted(set().union(*(ir["fields"] for ir in trees.values()))),
                "factors": {name: {"expression": emit(ir), "lookback_rows": ir["lookback"]}
                            for name, ir in trees.items()}}
    metadata["sha256"] = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    return trees, metadata


def evaluate_expressions(data, expressions, fields=ops.DEFAULT_FIELDS):
    trees, metadata = prepare(expressions, fields)
    ops.validate_frame(data, metadata["required_fields"])

    def evaluate(ir):
        if "field" in ir:
            return ops.field(data, ir["field"])
        if "literal" in ir:
            return ir["literal"]
        return ops.apply(ir["op"], *(evaluate(c) for c in ir["args"]))

    return ops.frame_result(data, {name: evaluate(ir) for name, ir in trees.items()})


def compile_factors(expressions, output, fields=ops.DEFAULT_FIELDS):
    trees, metadata = prepare(expressions, fields)
    output = Path(output).resolve()
    runtime_name = f"_factor_ops_{metadata['runtime_sha256'][:16]}.py"
    # Literals and operator calls are emitted from the validated tree, never pasted as code.
    calculations = ",\n        ".join(f"{name!r}: {emit(ir, python=True)}" for name, ir in trees.items())
    code = f'''"""Generated causal factors. Ship this file with {runtime_name}."""
import hashlib as _hashlib
import importlib.util as _importlib
from pathlib import Path as _Path
import pandas as pd

FACTOR_SPEC = {metadata!r}
_runtime = _Path(__file__).with_name({runtime_name!r})
if _hashlib.sha256(_runtime.read_bytes()).hexdigest() != FACTOR_SPEC["runtime_sha256"]:
    raise RuntimeError("Factor runtime hash mismatch; compile a new version after operator changes")
_spec = _importlib.spec_from_file_location("_factor_runtime", _runtime)
_ops = _importlib.module_from_spec(_spec)
_spec.loader.exec_module(_ops)


def compute_factors(data: pd.DataFrame) -> pd.DataFrame:
    """One instrument; same unique increasing DatetimeIndex, one column per factor version."""
    _ops.validate_frame(data, FACTOR_SPEC["required_fields"])
    return _ops.frame_result(data, {{
        {calculations}
    }})
'''
    runtime = output.with_name(runtime_name)
    artifacts = {output: code.encode(), runtime: RUNTIME_PATH.read_bytes()}
    if output == runtime:
        raise ValueError("Output must differ from the runtime file")
    for path, content in artifacts.items():
        if path.exists() and path.read_bytes() != content:
            raise FileExistsError(f"Preserve existing artifact; choose a new version path: {path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    for path, content in artifacts.items():
        path.write_bytes(content)
    return metadata | {"code_path": str(output), "runtime_path": str(runtime),
                       "code_sha256": hashlib.sha256(code.encode()).hexdigest()}


def operator_table():
    lines = ["| Operator | Parameters | Semantics |", "| --- | --- | --- |"]
    for name, entry in ops.OPERATORS.items():
        params = ", ".join(f"{n}: {kind}" for n, kind in entry["parameters"])
        lines.append(f"| `{name}` | `{params}` | {entry['description']} |")
    return "\n".join(lines)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate specification key: {key}")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("operators", "check", "compile"))
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--spec", type=Path, help="JSON with factors mapping and optional observed fields")
    sources.add_argument("--expression")
    parser.add_argument("--name", default="factor", help="Versioned output column for --expression")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "operators":
            print(operator_table()); return
        if args.spec:
            spec = json.loads(args.spec.read_text(), object_pairs_hook=unique_object)
            expressions, fields = spec["factors"], spec.get("fields", ops.DEFAULT_FIELDS)
        elif args.expression:
            expressions, fields = {args.name: args.expression}, ops.DEFAULT_FIELDS
        else:
            raise ValueError("Supply --expression or --spec")
        if args.action == "compile":
            if args.output is None:
                raise ValueError("compile requires --output")
            result = compile_factors(expressions, args.output, fields)
        else:
            result = prepare(expressions, fields)[1]
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError) as error:
        parser.exit(1, f"Factor expression failed: {error}\n")


if __name__ == "__main__":
    main()
