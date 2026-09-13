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


@pytest.fixture
def joint_manifest(manifest):
    from copy import deepcopy
    spec = json.loads(manifest.read_text())
    spec["schema"] = 2
    factor = spec["factors"][0]
    factor.update(family="response", hypothesis="Fixture response", role="return_prediction", parent_ids=[])
    strategy = spec["strategies"][0]
    strategy.update(family="response policy", hypothesis="Fixture consumer", parent_ids=[],
                    factor_roles={"F": "return_prediction"}, research_only=True)
    revised = deepcopy(factor)
    revised.update(id="F@2", parent_ids=["F"], formula="Revised fixture score")
    revised["metrics"][0]["pointer"] = "/revised_correlation"
    policy = deepcopy(strategy)
    policy.update(id="S@2", parent_ids=["S"], factor_ids=["F@2"], factor_roles={"F@2": "return_prediction"})
    spec["factors"].append(revised)
    spec["strategies"].append(policy)
    spec["routes"] = [{"id": "response", "hypothesis": "Fixture mechanism", "status": "active",
                       "factor_ids": ["F", "F@2"], "strategy_ids": ["S", "S@2"],
                       "diagnosis": "Factor improved; consumer effect pending", "next_step": "Compare consumer utility"}]
    spec["comparisons"] = [{"id": "revision", "route_id": "response", "parent_id": "F", "candidate_id": "F@2",
                            "diagnosis": "Positive paired difference", "decision": "Continue consumer evaluation",
                            "metrics": [{"label": "RankIC", "definition": "Matched fixture pairs", "unit": "ratio", "split": "train",
                                         "parent": {"source": "engine", "pointer": "/correlation"},
                                         "candidate": {"source": "engine", "pointer": "/revised_correlation"}}]}]
    engine = manifest.parent / "engine.json"
    data = json.loads(engine.read_text())
    data["revised_correlation"] = 0.2
    spec["sources"]["engine"]["sha256"] = save(engine, data)
    save(manifest, spec)
    return manifest


def test_joint_research_survives_compilation_rendering_and_download(joint_manifest, tmp_path):
    result = report.compile_report(joint_manifest, allow_synthetic=True)
    assert result["strategies"][0]["factor_ids"] == ["F"]
    assert result["strategies"][1]["factor_ids"] == ["F@2"]
    assert result["factors"][1]["parent_ids"] == ["F"]
    assert result["comparisons"][0]["metrics"][0]["delta"] == pytest.approx(0.2)
    output = tmp_path / "joint-public"
    report.render(result, output)
    exported = json.loads((output / "analysis.json").read_text())
    assert exported["routes"] == result["routes"]
    assert exported["comparisons"] == result["comparisons"]
    assert "revision,response,F,F@2,RankIC,train,0.0,0.2,0.2,ratio" in (output / "comparisons.csv").read_text()


@pytest.mark.parametrize("failure", ["cycle", "parent", "route", "role", "sealed", "unrelated_metric", "unexecuted"])
def test_joint_report_rejects_inconsistent_lineage_or_comparisons(joint_manifest, failure):
    spec = json.loads(joint_manifest.read_text())
    if failure == "cycle": spec["factors"][0]["parent_ids"] = ["F@2"]
    elif failure == "parent": spec["factors"][1]["parent_ids"] = ["S"]
    elif failure == "route": spec["routes"][0]["factor_ids"] = ["F"]
    elif failure == "role": spec["strategies"][0]["factor_roles"]["F"] = "risk"
    elif failure == "sealed": spec["comparisons"][0]["metrics"][0]["split"] = "test"
    elif failure == "unrelated_metric": spec["comparisons"][0]["metrics"][0]["parent"]["pointer"] = "/total_return"
    else: spec["factors"][1].update(status="proposed", metrics=[])
    save(joint_manifest, spec)
    with pytest.raises(ValueError): report.compile_report(joint_manifest, allow_synthetic=True)


@pytest.mark.parametrize("difference", ["statistic", "horizon", "aggregation", "label", "ambiguous"])
def test_paired_report_cannot_subtract_incompatible_metric_definitions(joint_manifest, difference):
    spec = json.loads(joint_manifest.read_text())
    metric = spec["factors"][1]["metrics"][0]
    if difference == "statistic":
        metric["definition"] = "Pearson instead of Spearman on fixture pairs"
    elif difference == "horizon":
        metric["definition"] = "Spearman on a different forward-return horizon"
    elif difference == "aggregation":
        metric["definition"] = "Pooled Spearman rather than equal-fold mean"
    elif difference == "label":
        metric["label"] = "Pearson IC"
    else:
        spec["factors"][1]["metrics"].append(dict(metric))
    save(joint_manifest, spec)
    with pytest.raises(ValueError, match="Comparison"):
        report.compile_report(joint_manifest, allow_synthetic=True)


def test_supporting_role_qualification_is_consumer_specific(joint_manifest):
    spec = json.loads(joint_manifest.read_text())
    spec["factors"][0].update(role="risk", status="admitted", qualified_strategy_ids=["S"])
    spec["strategies"][0].update(status="admitted", factor_roles={"F": "risk"}, research_only=False)
    save(joint_manifest, spec)
    result = report.compile_report(joint_manifest, allow_synthetic=True)
    assert result["factors"][0]["qualified_strategy_ids"] == ["S"]
    spec["strategies"][1].update(status="admitted", factor_ids=["F"], factor_roles={"F": "risk"}, research_only=False)
    save(joint_manifest, spec)
    with pytest.raises(ValueError, match="qualification does not cover"):
        report.compile_report(joint_manifest, allow_synthetic=True)


def test_rejected_factor_can_be_diagnosed_without_claiming_admission(joint_manifest):
    spec = json.loads(joint_manifest.read_text())
    spec["factors"][0]["status"] = "rejected"
    spec["strategies"][0]["research_only"] = True
    save(joint_manifest, spec)
    assert report.compile_report(joint_manifest, allow_synthetic=True)["strategies"][0]["research_only"]
    spec["strategies"][0]["status"] = "admitted"
    save(joint_manifest, spec)
    with pytest.raises(ValueError, match="Research-only"):
        report.compile_report(joint_manifest, allow_synthetic=True)


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
