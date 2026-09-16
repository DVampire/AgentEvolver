"""Numerical, causal and portability contracts for expression-generated factors."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agentevolver/skill/finance/factor_strategy_research_skill"


def load(path, name="generated_factor"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compiler = load(SKILL / "scripts/factor_expression.py", "compiler")


@pytest.fixture
def bars():
    return pd.DataFrame({"close": [1., 2., 2., 4., 3., 6.], "volume": [10., 20., 30., 20., 50., 60.]},
                        index=pd.date_range("2020-01-01", periods=6, tz="UTC"))


def test_hand_computed_values_missingness_ties_and_alignment(bars):
    before = bars.copy(deep=True)
    expressions = {"return": "close / delay(close, 1) - 1", "rank": "ts_rank(close, 3)",
                   "high_age": "ts_argmax(close, 3)", "low_age": "ts_arg_min(close, 3)",
                   "std": "ts_stddev(close, 3)", "cov": "ts_covariance(close, close, 3)"}
    result = compiler.evaluate_expressions(bars, expressions)
    np.testing.assert_allclose(result["return"], [np.nan, 1, 0, 1, -.25, 1], equal_nan=True)
    assert result["rank"].iloc[2] == pytest.approx(2.5 / 3)
    assert result["high_age"].iloc[2] == 0  # most recent of tied maxima
    assert result["low_age"].iloc[3] == 1   # most recent of tied minima
    assert result["std"].iloc[2] == pytest.approx(np.sqrt(1 / 3))
    assert result["cov"].iloc[2] == pytest.approx(1 / 3)
    assert result.index.equals(bars.index) and result.shape == (6, 6)
    assert_frame_equal(bars, before)


def test_missing_conditions_and_invalid_arithmetic_do_not_become_signals(bars):
    bars.loc[bars.index[1], "close"] = np.nan
    result = compiler.evaluate_expressions(bars, {
        "condition": "if_else(gt(close, 1), 1, 0)", "logic": "and_op(close, 0)",
        "zero_division": "close / 0", "bad_log": "log(-close)",
        "missing": "is_nan(close)", "constant_window": "ts_zscore(close * 0, 2)",
        "lookup": "kth_element(close, 3, 1)", "mean": "ts_mean(close, 3)",
    })
    assert np.isnan(result["condition"].iloc[1]) and np.isnan(result["logic"].iloc[1])
    assert result["missing"].iloc[1] == 1
    assert result[["zero_division", "bad_log", "constant_window"]].isna().all().all()
    assert result["lookup"].iloc[1] == 1  # explicit trailing lookup, no global fill
    assert np.isnan(result["mean"].iloc[3])
    assert not np.isinf(result.to_numpy()).any()


def test_generated_library_runs_after_move_and_pins_runtime(bars, tmp_path):
    definitions = {"F@1": "delta(close, 1)", "F@2": "ts_mean(delta(close, 1), 2)",
                   "quote'\nname": "volume / ts_mean(volume, 2)"}
    path = tmp_path / "author" / "factors.py"
    receipt = compiler.compile_factors(definitions, path)
    compiler.compile_factors(definitions, path)  # identical compile is an idempotent replay
    destination = tmp_path / "environment"
    shutil.copytree(path.parent, destination)
    generated = load(destination / path.name)
    assert_frame_equal(generated.compute_factors(bars), compiler.evaluate_expressions(bars, definitions))
    assert generated.FACTOR_SPEC["sha256"] == receipt["sha256"]
    with pytest.raises(FileExistsError):
        compiler.compile_factors({"new": "close * 2"}, path)
    runtime = destination / Path(receipt["runtime_path"]).name
    runtime.write_text(runtime.read_text() + "\n# changed\n")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        load(destination / path.name)


def test_copied_bundle_revision_uses_new_library_without_changing_parent(bars, tmp_path):
    import subprocess
    import sys
    parent = tmp_path / "parent" / "library.py"
    compiler.compile_factors({"F@1": "delta(close, 1)"}, parent)
    revised = tmp_path / "revision"
    shutil.copytree(parent.parent, revised)
    before = (revised / "library.py").read_bytes()
    spec = revised / "expression.json"
    spec.write_text(json.dumps({"factors": {"F@2": "ts_mean(close, 2)"}}))
    command = [sys.executable, str(SKILL / "scripts/factor_expression.py"), "compile", "--spec", str(spec)]
    failed = subprocess.run(command + ["--output", str(revised / "library.py")], capture_output=True, text=True)
    assert failed.returncode == 1 and "choose a new version path" in failed.stderr
    new_path = revised / "library_v2.py"
    success = subprocess.run(command + ["--output", str(new_path)], capture_output=True, text=True)
    assert success.returncode == 0, success.stderr
    assert (revised / "library.py").read_bytes() == before == parent.read_bytes()
    assert_frame_equal(load(new_path).compute_factors(bars), compiler.evaluate_expressions(bars, {"F@2": "ts_mean(close, 2)"}))


@pytest.mark.parametrize("expression", [
    "delay(close, -1)", "ts_mean(close, 0)", "ts_mean(close, 2.5)",
    "ts_mean(close, True)", "ts_quantile(close, 3, 1.1)", "kth_element(close, 2, 3)",
    "hump(close, 0)", "ts_mean(1, 2)", "rank(close)", "zscore(close)", "normalize(close)",
    "quantile(close, 0.5)", "close.shift(-1)", "close[0]", "__import__('os').getcwd()",
    "[x for x in close]", "close and volume", "future_return + close", "close ^ 2",
    "1e999 * close", "ts_mean(close, period=2, unknown=1)", "ts_mean(close, **{})", "1 + 2",
])
def test_invalid_or_lookahead_expressions_fail_before_emission(expression, tmp_path):
    with pytest.raises(ValueError):
        compiler.compile_factors({"F": expression}, tmp_path / "bad.py")
    assert not (tmp_path / "bad.py").exists()


@pytest.mark.parametrize("expression, replacement", [
    ("(close > 2) | (volume > 25)", "or_op(close > 2, volume > 25)"),
    ("(close > 2) & (volume > 25)", "and_op(close > 2, volume > 25)"),
    ("~(close > 2)", "not_op(close > 2)"),
    ("close > 2 or volume > 25", "or_op(close > 2, volume > 25)"),
    ("close > 2 and volume > 25", "and_op(close > 2, volume > 25)"),
    ("not (close > 2)", "not_op(close > 2)"),
])
def test_batch_logic_error_identifies_factor_and_recovers_without_partial_files(tmp_path, bars, expression, replacement):
    path = tmp_path / "compiled" / "library.py"
    definitions = {"F_valid@1": "close", "F_logic@2": expression}
    with pytest.raises(ValueError) as failed:
        compiler.compile_factors(definitions, path)
    assert "F_logic@2" in str(failed.value)
    assert replacement.split("(")[0] in str(failed.value)
    assert not path.parent.exists()
    definitions["F_logic@2"] = replacement
    compiler.compile_factors(definitions, path)
    actual = load(path).compute_factors(bars)["F_logic@2"]
    if replacement.startswith("or_op"):
        expected = (bars.close > 2) | (bars.volume > 25)
    elif replacement.startswith("and_op"):
        expected = (bars.close > 2) & (bars.volume > 25)
    else:
        expected = ~(bars.close > 2)
    np.testing.assert_array_equal(actual, expected.astype(float))


def test_every_registered_operator_is_causal_and_matches_generated_code(bars, tmp_path):
    # This traverses the supported surface using real calculations, not mocked dispatch.
    expressions = {}
    for name, entry in compiler.ops.OPERATORS.items():
        args = []
        for key, kind in entry["parameters"]:
            args.append("volume" if key == "y" else "close" if kind in ("series", "value")
                        else "3" if kind in ("window", "lag") else "1" if kind == "k" else "0.5")
        expressions[name] = f"{name}({', '.join(args)})"
    full = compiler.evaluate_expressions(bars, expressions)
    prefix = compiler.evaluate_expressions(bars.iloc[:4], expressions)
    assert_frame_equal(full.iloc[:4], prefix)
    perturbed = bars.copy()
    perturbed.iloc[4:] *= 13
    assert_frame_equal(full.iloc[:4], compiler.evaluate_expressions(perturbed, expressions).iloc[:4])
    path = tmp_path / "all_ops.py"
    compiler.compile_factors(expressions, path)
    assert_frame_equal(full, load(path).compute_factors(bars))


def test_canonical_identity_and_lookback(bars):
    a = compiler.prepare({"F": "close / delay(close, 2) - 1"})[1]
    b = compiler.prepare({"F": "subtract(divide(close, ts_delay(close, period=2)), 1)"})[1]
    assert a["sha256"] == b["sha256"]
    assert a["factors"]["F"]["lookback_rows"] == 2
    c = compiler.prepare({"F": "ts_mean(delta(close, 2), 5)"})[1]
    assert c["factors"]["F"]["lookback_rows"] == 6
    assert c["sha256"] != a["sha256"]
    assert compiler.prepare({"F": "hump(close, 0.1)"})[1]["factors"]["F"]["lookback_rows"] is None
    supplied = bars.assign(observed_spread=0.1)
    assert compiler.evaluate_expressions(supplied, {"F": "close / observed_spread"},
                                         fields=["close", "observed_spread"]).shape == (6, 1)


@pytest.mark.parametrize("defect", ["reversed", "duplicates", "multiasset", "numeric_index", "inf", "strings", "missing"])
def test_bad_input_frames_do_not_silently_realign_or_mix_assets(bars, defect):
    if defect == "reversed": bars = bars.iloc[::-1]
    elif defect == "duplicates": bars.index = pd.DatetimeIndex([bars.index[0]] * len(bars))
    elif defect == "multiasset": bars["symbol"] = ["A", "B"] * 3
    elif defect == "numeric_index": bars = bars.reset_index(drop=True)
    elif defect == "inf": bars.iloc[0, 0] = np.inf
    elif defect == "strings": bars["close"] = "1"
    else: bars = bars.drop(columns="close")
    with pytest.raises(ValueError):
        compiler.evaluate_expressions(bars, {"F": "close + 1"})


def test_operator_reference_matches_executable_signatures():
    text = (SKILL / "references/factor-expressions.md").read_text()
    assert text.split("<!-- operator-table -->\n", 1)[1].strip() == compiler.operator_table()


def test_cli_compiles_batch_and_rejects_ambiguous_factor_names(tmp_path, bars):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"factors": {"F@1": "delta(close, 1)", "F@2": "ts_mean(close, 2)"}}))
    output = tmp_path / "library.py"
    command = [sys.executable, str(SKILL / "scripts/factor_expression.py"), "compile",
               "--spec", str(spec), "--output", str(output)]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    assert Path(json.loads(completed.stdout)["code_path"]) == output
    assert list(load(output).compute_factors(bars)) == ["F@1", "F@2"]
    spec.write_text('{"factors":{"F":"close", "F":"volume"}}')
    invalid = subprocess.run(command, capture_output=True, text=True)
    assert invalid.returncode == 1 and "Duplicate specification key" in invalid.stderr
    assert list(load(output).compute_factors(bars)) == ["F@1", "F@2"]
