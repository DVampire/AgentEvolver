"""Saved datasets and numerical outputs must survive the actual report boundary."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "agentevolver/skill/finance/factor_strategy_research_skill/scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


report = module("report")
snapshot = module("check_snapshot")


def save(path, value):
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def manifest(tmp_path):
    # Hand-computable engineering observations; never represented as market research.
    source = {"scope": "synthetic", "correlation": 0.0, "total_return": -0.01,
              "folds": [{"fold": "A", "value": -0.2}, {"fold": "B", "value": 0.2}],
              "equity": [{"date": f"2020-01-0{i+2}", "value": v} for i, v in enumerate([100, 110, 99])]}
    digest = save(tmp_path / "engine.json", source)
    spec = {"schema": 1, "study_id": "fixture", "scope": "synthetic", "data_basis": "Hand-computable fixture",
            "strict_data": {"status": "unmet", "reasons": ["Synthetic data"]}, "test_state": "sealed",
            "sources": {"engine": {"path": "engine.json", "sha256": digest}},
            "factors": [{"id": "F", "formula": "fixture score", "status": "evaluated", "metrics": [
                {"label": "RankIC", "definition": "Spearman on fixture pairs", "unit": "ratio", "split": "train", "source": "engine", "pointer": "/correlation"}]}],
            "strategies": [{"id": "S", "rules": "Fixture long/cash", "status": "evaluated", "factor_ids": ["F"], "metrics": [
                {"label": "Total return", "definition": "Final / initial equity - 1", "unit": "percent", "split": "train", "source": "engine", "pointer": "/total_return"}]}],
            "charts": [
                {"id": "folds", "title": "Fold correlations", "kind": "bar", "section": "factors", "split": "train", "x_label": "Fold", "y_label": "RankIC", "series": [
                    {"label": "Factor", "source": "engine", "pointer": "/folds", "x": "/fold", "y": "/value"}]},
                {"id": "equity", "title": "Equity", "kind": "line", "section": "strategies", "split": "train", "x_label": "Date", "y_label": "Fixture units", "series": [
                    {"label": "Net", "source": "engine", "pointer": "/equity", "x": "/date", "y": "/value"}]}]}
    save(tmp_path / "manifest.json", spec)
    return tmp_path / "manifest.json"


def test_measured_zero_survives_and_exports_match_sources(manifest, tmp_path):
    result = report.compile_report(manifest, allow_synthetic=True)
    assert result["factors"][0]["metrics"][0]["value"] == 0
    assert result["strategies"][0]["metrics"][0]["value"] == -0.01
    output = tmp_path / "public"
    report.render(result, output)
    assert (output / "visual.css").read_bytes() == (ROOT / "agentevolver/visual/benchmark/style.css").read_bytes()
    assert ",-0.01,percent," in (output / "metrics.csv").read_text()
    assert "2020-01-04,99" in (output / "series.csv").read_text()
    assert json.loads((output / "analysis.json").read_text())["charts"][1]["series"][0]["points"][-1]["y"] == 99


@pytest.mark.parametrize("failure", ["hash", "nulls", "empty_chart", "scope", "sealed_test", "bad_binding", "missing_definition"])
def test_report_rejects_invalid_research_payloads(manifest, failure):
    spec = json.loads(manifest.read_text())
    engine = manifest.parent / "engine.json"
    data = json.loads(engine.read_text())
    if failure == "hash":
        spec["sources"]["engine"]["sha256"] = "wrong"
    elif failure == "nulls":
        data["correlation"] = None
        spec["factors"][0]["metrics"][0]["reason"] = "not calculated"
    elif failure == "empty_chart":
        data["equity"] = []
    elif failure == "scope":
        spec["scope"] = "research"
    elif failure == "sealed_test":
        spec["charts"][1]["split"] = "test"
    elif failure == "bad_binding":
        spec["strategies"][0]["factor_ids"] = ["missing"]
    else:
        del spec["strategies"][0]["metrics"][0]["definition"]
    if failure != "hash":
        spec["sources"]["engine"]["sha256"] = save(engine, data)
    save(manifest, spec)
    with pytest.raises(ValueError):
        report.compile_report(manifest, allow_synthetic=True)


def test_synthetic_needs_explicit_opt_in_and_cash_baseline_is_legal(manifest):
    with pytest.raises(ValueError, match="Synthetic"):
        report.compile_report(manifest)
    spec = json.loads(manifest.read_text())
    spec["strategies"][0].update(baseline=True, factor_ids=[])
    save(manifest, spec)
    assert report.compile_report(manifest, allow_synthetic=True)["strategies"][0]["baseline"]


@pytest.mark.parametrize("defect", [None, "missing", "duplicate", "nan", "symbol", "adjustment", "hash"])
def test_local_snapshot_accepts_holiday_bounds_and_rejects_bad_data(tmp_path, defect):
    bars = [{"date": f"2020-01-0{day}", "symbol": "FIX", "open": 10, "high": 12, "low": 9,
             "close": 11, "volume": 0, "adjusted_close": 5.5} for day in (2, 3)]
    if defect == "missing": bars.pop()
    if defect == "duplicate": bars.append(bars[0])
    if defect == "nan": bars[0]["volume"] = float("nan")
    if defect == "symbol": bars[0]["symbol"] = "WRONG"
    if defect == "adjustment": bars[0]["adjusted_close"] = None
    path = tmp_path / "saved.json"
    digest = save(path, {"result": {"bars": bars}})
    kwargs = dict(sha256="wrong" if defect == "hash" else digest, symbol="FIX", start="2020-01-01", end="2020-01-05", adjusted_close="adjusted_close")
    if defect:
        with pytest.raises(ValueError): snapshot.check_snapshot(path, **kwargs)
    else:
        check = snapshot.check_snapshot(path, **kwargs)
        assert check["rows"] == 2 and check["first_session"] == "2020-01-02"
        assert check["last_session"] == "2020-01-03"
