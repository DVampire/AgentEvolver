"""Causal, single-instrument factor operators; pandas/numpy are the only dependencies.

Operator concepts were reviewed against FactorStrategyLLM's ops.py. This implementation
defines its own missing-value, alignment and causal semantics; it does not import that repo.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

VERSION = "1.0.0"
DEFAULT_FIELDS = ("open", "high", "low", "close", "volume")

# Parameter types also drive expression validation and the generated operator table.
# value: scalar or Series; series: observed vector; window/lag/k: literal integer;
# fraction: literal [0,1]; positive: literal > 0.
OPERATORS = {}


def register(names, parameters, description):
    for name in names.split():
        OPERATORS[name] = {"parameters": parameters, "description": description}


for name, description in {
    "abs": "Absolute value.", "reverse": "Negation.", "sign": "-1, 0 or 1; missing stays missing.",
    "log": "Natural logarithm; nonpositive input is missing.",
    "sqrt": "Square root; negative input is missing.", "is_nan": "1 for missing, otherwise 0.",
    "not_op": "1 for zero, 0 for nonzero; missing stays missing.",
}.items():
    register(name, (("x", "value"),), description)
for name, description in {
    "add": "x + y; missing values propagate.", "subtract": "x - y.",
    "multiply": "x * y; missing values propagate.",
    "divide": "x / y; zero denominator yields missing, without epsilon adjustment.",
    "power": "x ** y; invalid or nonfinite real results become missing.",
    "signed_power": "sign(x) * abs(x) ** y.",
    "min": "Elementwise minimum; missing propagates.",
    "max": "Elementwise maximum; missing propagates.",
    "lt": "x < y.", "le": "x <= y.", "eq": "x == y.",
    "gt": "x > y.", "ge": "x >= y.", "ne": "x != y.",
    "and_op": "Nonzero x AND nonzero y; either missing yields missing.",
    "or_op": "Nonzero x OR nonzero y; either missing yields missing.",
}.items():
    register(name, (("x", "value"), ("y", "value")), description)
register("if_else", (("condition", "value"), ("yes", "value"), ("no", "value")),
         "Select yes for nonzero condition, else no; missing condition yields missing.")
register("delay ts_delay", (("x", "series"), ("period", "lag")), "Past shift by period rows; zero allowed, negative forbidden.")
register("delta ts_delta", (("x", "series"), ("period", "lag")), "x[t] - x[t-period].")
for name, description in {
    "ts_sum": "Trailing sum.", "ts_mean": "Trailing mean.",
    "ts_min": "Trailing minimum.", "ts_max": "Trailing maximum.",
    "ts_product": "Trailing product.",
    "ts_stddev": "Trailing sample standard deviation (ddof=1); needs at least two rows.",
    "ts_std_dev": "Alias of ts_stddev.",
    "ts_rank": "Current value's percentile rank in trailing window; average ties / period.",
    "ts_argmax": "Rows since the most recent maximum in trailing window; current = 0.",
    "ts_argmin": "Rows since the most recent minimum in trailing window; current = 0.",
    "ts_arg_min": "Alias of ts_argmin.",
    "ts_zscore": "(x - trailing mean) / sample std; zero variance yields missing.",
    "ts_av_diff": "x - trailing mean; requires a complete window.",
    "ts_normalize": "(x - trailing min) / (trailing max - min); constant window is missing.",
}.items():
    register(name, (("x", "series"), ("period", "window")), description)
register("ts_corr ts_covariance", (("x", "series"), ("y", "series"), ("period", "window")),
         "Trailing Pearson correlation / sample covariance (ddof=1), on complete paired windows.")
register("ts_quantile", (("x", "series"), ("period", "window"), ("q", "fraction")),
         "Trailing quantile with linear interpolation.")
register("kth_element", (("x", "series"), ("period", "window"), ("k", "k")),
         "k-th most recent finite value in trailing window, k=1 includes today; partial windows allowed.")
register("last_diff_value", (("x", "series"), ("period", "window")),
         "Most recent finite past value different from current within window; partial windows allowed.")
register("days_from_last_change", (("x", "series"),),
         "Rows since last adjacent finite change; first valid row or row after missing starts at 0. Stateful.")
register("hump", (("x", "series"), ("limit", "positive")),
         "Cap each output's change from prior output to +/- limit; missing resets state. Stateful.")


def validate_frame(data, fields):
    if not isinstance(data, pd.DataFrame):
        raise TypeError("Factor input must be a DataFrame")
    if (not isinstance(data.index, pd.DatetimeIndex) or data.index.hasnans
            or not data.index.is_unique or not data.index.is_monotonic_increasing):
        raise ValueError("Use a unique increasing DatetimeIndex for one instrument; do not sort silently")
    if not data.columns.is_unique:
        raise ValueError("Duplicate input columns")
    for key in ("symbol", "ticker", "instrument"):
        if key in data and data[key].nunique(dropna=False) > 1:
            raise ValueError("Evaluate instruments separately; rolling windows cannot cross instruments")
    for field in fields:
        if field not in data:
            raise ValueError(f"Missing observed input field: {field}")
        if (not pd.api.types.is_numeric_dtype(data[field])
                or pd.api.types.is_bool_dtype(data[field])
                or pd.api.types.is_complex_dtype(data[field])):
            raise ValueError(f"Input field must be real numeric: {field}")
        values = data[field].to_numpy(dtype=float, na_value=np.nan)
        if np.isinf(values).any():
            raise ValueError(f"Infinite input field: {field}")


def field(data, name):
    return data[name].astype(float)


def clean(value):
    if isinstance(value, pd.Series):
        return value.astype(float).replace([np.inf, -np.inf], np.nan)
    return float(value) if np.isfinite(value) else float("nan")


def frame_result(data, values):
    result = {}
    for name, value in values.items():
        if isinstance(value, pd.Series) and not value.index.equals(data.index):
            raise ValueError(f"Factor index changed: {name}")
        result[name] = clean(value)
    return pd.DataFrame(result, index=data.index)


def _where(condition, yes, no):
    index = next((v.index for v in (condition, yes, no) if isinstance(v, pd.Series)), None)
    result = np.where(pd.isna(condition), np.nan, np.where(condition != 0, yes, no))
    return pd.Series(result, index=index) if index is not None else float(result)


def apply(name, *args):
    """Execute a validated operator, retaining missingness and exact index alignment."""
    if name not in OPERATORS:
        raise ValueError(f"Unknown operator: {name}")
    params = OPERATORS[name]["parameters"]
    if len(args) != len(params):
        raise ValueError(f"Wrong argument count for {name}")
    indexed = [x for x in args if isinstance(x, pd.Series)]
    if any(not x.index.equals(indexed[0].index) for x in indexed[1:]):
        raise ValueError("Operator inputs must have identical indexes")
    for (key, kind), value in zip(params, args):
        if kind == "series" and not isinstance(value, pd.Series):
            raise ValueError(f"{name}.{key} requires a series")
        if kind in ("window", "lag", "k"):
            if type(value) is not int or value < (0 if kind == "lag" else 1):
                raise ValueError(f"{name}.{key} requires a {'nonnegative' if kind == 'lag' else 'positive'} integer")
        if kind in ("fraction", "positive"):
            if (type(value) not in (int, float) or not np.isfinite(value)
                    or (not 0 <= value <= 1 if kind == "fraction" else value <= 0)):
                raise ValueError(f"Invalid {name}.{key}")
    if name == "kth_element" and args[2] > args[1]:
        raise ValueError("k cannot exceed the window")
    with np.errstate(all="ignore"):
        return clean(_apply(name, *args))


def _apply(name, x, *rest):
    unary = {"abs": np.abs, "reverse": np.negative, "sign": np.sign,
             "log": np.log, "sqrt": np.sqrt, "is_nan": pd.isna}
    if name in unary:
        return unary[name](x)
    if name == "not_op":
        return _where(x, 0, 1)
    if name == "if_else":
        return _where(x, *rest)
    binary = {"add": np.add, "subtract": np.subtract, "multiply": np.multiply,
              "divide": np.divide, "power": np.power, "min": np.minimum, "max": np.maximum}
    if name in binary:
        return binary[name](x, rest[0])
    if name == "signed_power":
        return np.sign(x) * np.power(np.abs(x), rest[0])
    comparisons = {"lt": np.less, "le": np.less_equal, "eq": np.equal,
                   "gt": np.greater, "ge": np.greater_equal, "ne": np.not_equal}
    if name in comparisons or name in ("and_op", "or_op"):
        y = rest[0]
        result = (comparisons[name](x, y) if name in comparisons else
                  np.logical_and(x != 0, y != 0) if name == "and_op" else np.logical_or(x != 0, y != 0))
        return _where(pd.isna(x) | pd.isna(y), np.nan, result)
    if name in ("delay", "ts_delay", "delta", "ts_delta"):
        shifted = x.shift(rest[0])
        return shifted if name in ("delay", "ts_delay") else x - shifted
    if name in ("days_from_last_change", "hump"):
        result, previous, age = [], np.nan, 0
        for value in x:
            if pd.isna(value):
                result.append(np.nan); previous, age = np.nan, 0
            elif name == "hump":
                previous = value if pd.isna(previous) else previous + np.clip(value - previous, -rest[0], rest[0])
                result.append(previous)
            else:
                age = age + 1 if value == previous else 0
                result.append(age); previous = value
        return pd.Series(result, index=x.index, dtype=float)
    if name in ("ts_corr", "ts_covariance"):
        y, period = rest
        paired = x.notna() & y.notna()
        roll = x.where(paired).rolling(period, min_periods=period)
        return roll.corr(y.where(paired)) if name == "ts_corr" else roll.cov(y.where(paired), ddof=1)
    period = rest[0]
    roll = x.rolling(period, min_periods=period)
    reductions = {"ts_sum": "sum", "ts_mean": "mean", "ts_min": "min", "ts_max": "max"}
    if name in reductions:
        return getattr(roll, reductions[name])()
    if name in ("ts_stddev", "ts_std_dev"):
        return roll.std(ddof=1)
    if name == "ts_rank":
        return roll.rank(method="average", pct=True)
    if name == "ts_product":
        return roll.apply(np.prod, raw=True)
    if name in ("ts_argmax", "ts_argmin", "ts_arg_min"):
        fn = np.argmax if name == "ts_argmax" else np.argmin
        return roll.apply(lambda values: float(fn(values[::-1])), raw=True)
    if name == "ts_av_diff":
        return x - roll.mean()
    if name == "ts_zscore":
        return (x - roll.mean()) / roll.std(ddof=1)
    if name == "ts_normalize":
        return (x - roll.min()) / (roll.max() - roll.min())
    if name == "ts_quantile":
        return roll.quantile(rest[1], interpolation="linear")
    if name == "kth_element":
        k = rest[1]
        def kth(values):
            valid = values[np.isfinite(values)]
            return valid[-k] if len(valid) >= k else np.nan
        return x.rolling(period, min_periods=1).apply(kth, raw=True)
    if name == "last_diff_value":
        def last(values):
            if not np.isfinite(values[-1]):
                return np.nan
            different = values[:-1][np.isfinite(values[:-1]) & (values[:-1] != values[-1])]
            return different[-1] if len(different) else np.nan
        return x.rolling(period, min_periods=1).apply(last, raw=True)
    raise ValueError(f"Operator has no implementation: {name}")
