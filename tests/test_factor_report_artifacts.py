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
strategy_spec = module("strategy_spec")


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


@pytest.fixture
def strategy_definition(tmp_path):
    # The checker hashes files without importing or executing a policy.
    code = b'raise RuntimeError("archive validation must not execute policy code")\n'
    (tmp_path / "policy.py").write_bytes(code)
    return {
        "schema": 1, "strategy_id": "S001", "version": "v001", "id": "S001@v001",
        "name": "回撤修复 / Pullback recovery", "description": "A fixture strategy with a complete archived design.",
        "family": "event recovery", "hypothesis": "The declared fixture response supports a bounded event policy.",
        "falsification": "No incremental utility versus the fixed reference.",
        "created_round": "R001", "parent_ids": [],
        "change": {"kind": "initial", "summary": "Initial hypothesis", "reason": "Cover an untested mechanism", "evidence_ids": []},
        "factor_bindings": [{"factor_id": "F001@v001", "role": "return_prediction", "purpose": "Trigger a candidate entry"}],
        "design": {
            "objective": "Test incremental response under unchanged costs",
            "mechanism": "A declared event selects exposure rather than a full-history fitted direction.",
            "combination": "Use the single entry factor", "fit_policy": "Fit only on the permitted past fold",
            "pseudocode": "Observe completed close; form target; fill at next open.",
            "assumptions": ["Fixture units are not market prices"], "failure_modes": ["No sample support"],
            "rules": {"entry": "Positive signal", "exit": "Nonpositive signal", "sizing": "One unit",
                      "rebalance": "Daily", "neutral": "Cash", "risk": "No leverage", "execution": "Next open"}},
        "parameters": {"threshold": 0},
        "implementation": {"path": "policy.py", "sha256": hashlib.sha256(code).hexdigest(), "entrypoint": "target"},
        "extensions": {"design_notes": "Open additional design metadata is retained"},
    }


@pytest.fixture
def archived_manifest(manifest, strategy_definition):
    spec = json.loads(manifest.read_text())
    spec["schema"] = 3
    spec["factors"][0].update(id="F001@v001", name="Fixture response", family="response",
                               hypothesis="Fixture response", parent_ids=[], role="return_prediction")
    strategy = spec["strategies"][0]
    for key in ("id", "rules", "factor_ids"):
        del strategy[key]
    strategy.update(strategy_spec={"source": "engine", "pointer": "/strategy_spec"}, research_only=True)
    spec["routes"] = [{"id": "recovery", "hypothesis": "Fixture recovery", "status": "active",
                       "factor_ids": ["F001@v001"], "strategy_ids": ["S001@v001"],
                       "diagnosis": "Need more evidence", "next_step": "Review contribution"}]
    engine = manifest.parent / "engine.json"
    data = json.loads(engine.read_text())
    data["strategy_spec"] = strategy_definition
    spec["sources"]["engine"]["sha256"] = save(engine, data)
    save(manifest, spec)
    return manifest


def test_strategy_definition_and_source_identity_survive_report_export(archived_manifest, tmp_path):
    result = report.compile_report(archived_manifest, allow_synthetic=True)
    row = result["strategies"][0]
    original = json.loads((archived_manifest.parent / "engine.json").read_text())["strategy_spec"]
    assert row["strategy_spec"] == original
    assert row["name"] == original["name"]
    assert row["description"] == original["description"]
    assert row["version"] == "v001" and row["created_round"] == "R001"
    assert row["factor_ids"] == ["F001@v001"]
    assert row["spec_sha256"] == strategy_spec.digest(original)
    assert row["definition_source"] == {"source": "engine", "pointer": "/strategy_spec"}
    assert row["metrics"][0]["value"] == -0.01
    output = tmp_path / "archived-report"
    report.render(result, output)
    assert json.loads((output / "analysis.json").read_text())["strategies"][0] == row


@pytest.mark.parametrize("key,value", [("name", "Wrong display name"), ("id", "S002@v001"),
                                      ("factor_ids", ["OTHER@v001"]), ("rules", "Different execution")])
def test_report_rejects_metadata_diverging_from_archived_strategy(archived_manifest, key, value):
    spec = json.loads(archived_manifest.read_text())
    spec["strategies"][0][key] = value
    save(archived_manifest, spec)
    with pytest.raises(ValueError, match="archive conflicts"):
        report.compile_report(archived_manifest, allow_synthetic=True)


@pytest.mark.parametrize("defect", ["name", "description", "design", "pseudocode", "latest", "id",
                                  "self_parent", "change", "parameters", "implementation", "nonfinite"])
def test_incomplete_strategy_archives_fail_before_report_export(archived_manifest, defect):
    spec = json.loads(archived_manifest.read_text())
    engine = archived_manifest.parent / "engine.json"
    data = json.loads(engine.read_text())
    definition = data["strategy_spec"]
    if defect in ("name", "description", "design"):
        del definition[defect]
    elif defect == "pseudocode": definition["design"]["pseudocode"] = " "
    elif defect == "latest": definition["factor_bindings"][0]["factor_id"] = "F001@latest"
    elif defect == "id": definition["id"] = "S001"
    elif defect == "self_parent": definition["parent_ids"] = [definition["id"]]
    elif defect == "change": definition["change"]["kind"] = "policy_revision"
    elif defect == "parameters": definition["parameters"] = []
    elif defect == "implementation": definition["implementation"] = None
    else: definition["parameters"]["threshold"] = float("nan")
    spec["sources"]["engine"]["sha256"] = save(engine, data)
    save(archived_manifest, spec)
    with pytest.raises(ValueError):
        report.compile_report(archived_manifest, allow_synthetic=True)


def test_archive_checker_checks_hashes_and_distinguishes_proposals(strategy_definition, tmp_path):
    path = tmp_path / "spec.json"
    dependency = tmp_path / "helper.py"
    dependency.write_text("# fixture dependency\n")
    strategy_definition["implementation"]["dependencies"] = [
        {"path": dependency.name, "sha256": hashlib.sha256(dependency.read_bytes()).hexdigest()}]
    save(path, strategy_definition)
    receipt = strategy_spec.check_file(path, require_implementation=True)
    assert receipt["implementation_hashes_verified"] and receipt["spec_sha256"] == strategy_spec.digest(strategy_definition)
    path.write_text(json.dumps(strategy_definition, indent=2, ensure_ascii=False))
    assert strategy_spec.check_file(path) == receipt  # Formatting does not change the definition identity.
    dependency.write_text("# changed dependency\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        strategy_spec.check_file(path)
    strategy_definition["implementation"] = None
    save(path, strategy_definition)
    assert not strategy_spec.check_file(path)["implementation_hashes_verified"]
    with pytest.raises(ValueError, match="pinned implementation"):
        strategy_spec.check_file(path, require_implementation=True)


def test_strategy_archive_cli_returns_queryable_receipt_and_actionable_error(strategy_definition, tmp_path):
    import subprocess
    import sys

    path = tmp_path / "spec.json"
    save(path, strategy_definition)
    command = [sys.executable, str(SCRIPTS / "strategy_spec.py"), str(path), "--require-implementation"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["id"] == "S001@v001" and receipt["spec_path"] == str(path)
    assert receipt["implementation_hashes_verified"]
    del strategy_definition["description"]
    save(path, strategy_definition)
    failed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert failed.returncode == 1 and "description" in failed.stderr and not failed.stdout


def test_strategy_revision_retains_both_designs_and_parent_bindings(archived_manifest):
    from copy import deepcopy
    spec = json.loads(archived_manifest.read_text())
    engine = archived_manifest.parent / "engine.json"
    data = json.loads(engine.read_text())
    child = deepcopy(data["strategy_spec"])
    child.update(version="v002", id="S001@v002", created_round="R002", parent_ids=["S001@v001"])
    child["change"] = {"kind": "policy_revision", "summary": "Bound event holding", "reason": "Diagnosed stale exposure",
                       "evidence_ids": ["E001"]}
    child["design"]["rules"]["exit"] = "Nonpositive signal or holding limit"
    data["revised_spec"] = child
    row = deepcopy(spec["strategies"][0])
    row["strategy_spec"]["pointer"] = "/revised_spec"
    spec["strategies"].append(row)
    spec["routes"][0]["strategy_ids"].append("S001@v002")
    spec["sources"]["engine"]["sha256"] = save(engine, data)
    save(archived_manifest, spec)
    rows = report.compile_report(archived_manifest, allow_synthetic=True)["strategies"]
    assert rows[0]["strategy_spec"]["design"]["rules"]["exit"] == "Nonpositive signal"
    assert rows[1]["parent_ids"] == [rows[0]["id"]]
    assert rows[1]["strategy_spec"] == child
    assert rows[0]["spec_sha256"] != rows[1]["spec_sha256"]


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


def test_round_report_is_queryable_json_and_versions_cannot_be_overwritten(joint_manifest, tmp_path):
    spec = json.loads(joint_manifest.read_text())
    spec["record"] = {"round_id": "R002", "report_id": "R002-v001", "phase": "joint_refinement",
                      "parent_report_id": "R001-v001"}
    save(joint_manifest, spec)
    result = report.compile_report(joint_manifest, allow_synthetic=True)
    folder = tmp_path / "rounds" / "R002" / "reports" / "v001"
    receipt = report.render(result, folder)
    path = Path(receipt["analysis_path"])
    original = path.read_bytes()
    modified = path.stat().st_mtime_ns
    exported = json.loads(original)
    assert exported["record"] == spec["record"]
    assert exported["strategies"][1]["factor_ids"] == ["F@2"]
    assert exported["comparisons"][0]["metrics"][0]["delta"] == pytest.approx(0.2)
    assert hashlib.sha256(original).hexdigest() == receipt["analysis_sha256"]
    assert report.render(result, folder) == receipt
    assert path.stat().st_mtime_ns == modified
    result["summary"] = "Corrected interpretation; numerical results unchanged"
    result["record"] = dict(result["record"], report_id="R002-v002", parent_report_id="R002-v001")
    with pytest.raises(ValueError, match="use a new version directory"):
        report.render(result, folder)
    assert path.read_bytes() == original
    corrected = report.render(result, folder.parent / "v002")
    assert json.loads(Path(corrected["analysis_path"]).read_text())["record"]["report_id"] == "R002-v002"
    assert not list(folder.parent.glob(".v00*-*"))


def test_report_failure_does_not_publish_partial_version(manifest, tmp_path, monkeypatch):
    result = report.compile_report(manifest, allow_synthetic=True)
    folder = tmp_path / "rounds" / "R001" / "reports" / "v001"
    copy = report.shutil.copyfile

    def fail_copy(source, dest):
        if Path(dest).name == "report.js":
            raise OSError("interrupted asset copy")
        return copy(source, dest)

    monkeypatch.setattr(report.shutil, "copyfile", fail_copy)
    with pytest.raises(OSError, match="interrupted"):
        report.render(result, folder)
    assert not folder.exists()
    assert list(folder.parent.iterdir()) == []
    monkeypatch.setattr(report.shutil, "copyfile", copy)
    assert Path(report.render(result, folder)["analysis_path"]).is_file()


@pytest.mark.parametrize("record", [None, [], {}, {"round_id": "R001", "report_id": " ", "phase": "screening"}])
def test_report_rejects_incomplete_round_identity(manifest, record):
    spec = json.loads(manifest.read_text())
    spec["record"] = record
    save(manifest, spec)
    with pytest.raises(ValueError):
        report.compile_report(manifest, allow_synthetic=True)


def test_large_joint_inventory_survives_json_and_csv_without_a_browser(joint_manifest, tmp_path):
    # Scale the artifact boundary with fixtures; this is not evidence of market diversity.
    from copy import deepcopy
    import csv

    spec = json.loads(joint_manifest.read_text())
    factor, strategy = deepcopy(spec["factors"][0]), deepcopy(spec["strategies"][0])
    spec["factors"] = []
    spec["strategies"] = []
    spec["comparisons"] = []
    for i in range(100):
        spec["factors"].append(dict(deepcopy(factor), id=f"F{i:03d}@1"))
    for i in range(25):
        ids = [f"F{j:03d}@1" for j in range(i * 4, i * 4 + 4)]
        spec["strategies"].append(dict(deepcopy(strategy), id=f"S{i:03d}@1", factor_ids=ids,
                                       factor_roles={fid: "return_prediction" for fid in ids}))
    spec["routes"][0].update(factor_ids=[c["id"] for c in spec["factors"]],
                             strategy_ids=[c["id"] for c in spec["strategies"]])
    save(joint_manifest, spec)
    result = report.compile_report(joint_manifest, allow_synthetic=True)
    folder = tmp_path / "large-report"
    report.render(result, folder)
    data = json.loads((folder / "analysis.json").read_text())
    assert len(data["factors"]) == 100 and len(data["strategies"]) == 25
    assert data["strategies"][-1]["factor_ids"][-1] == "F099@1"
    with (folder / "metrics.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 125
    assert {r["candidate"] for r in rows} == {c["id"] for c in data["factors"] + data["strategies"]}
    # Human visualization still loads the very same JSON via the existing shell/script.
    from bs4 import BeautifulSoup
    page = BeautifulSoup((folder / "index.html").read_text(), "html.parser")
    assert page.select_one('script[src="report.js"]')
    assert page.select_one("#factors") and page.select_one("#strategies")
    for tag in page.select("script[src], link[href]"):
        assert (folder / (tag.get("src") or tag["href"])).is_file()


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
