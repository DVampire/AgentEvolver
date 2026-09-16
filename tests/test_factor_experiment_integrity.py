"""Chronology, durable completion and frozen access without market data or a model."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "agentevolver/skill/finance/factor_strategy_research_skill/scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


plans = module("research_plan")
records = module("trial_records")


@pytest.fixture
def chronology():
    sessions = [f"2020-01-{day:02d}" for day in (2, 3, 6, 7, 8, 9, 10, 13, 14, 15)]
    study = {"study_id": "fixture", "splits": {"train": ["2020-01-01", "2020-01-15"],
                                               "test": ["2020-01-16", "2020-01-31"]}}
    plan = {"schema": 1, "study_id": "fixture", "train_sessions_sha256": plans.digest(sessions),
            "windows": [{"id": "short", "fit": ["2020-01-02", "2020-01-06"],
                         "score": ["2020-01-08", "2020-01-09"], "gap_sessions": 1},
                        {"id": "later", "fit": ["2020-01-03", "2020-01-09"],
                         "score": ["2020-01-13", "2020-01-15"], "gap_sessions": 1}]}
    return study, plan, sessions


def test_nonannual_agent_windows_and_actual_combination_fit_rows(chronology):
    study, plan, sessions = chronology
    receipt = plans.validate_plan(study, plan, sessions)
    assert receipt["window_ids"] == ["short", "later"] and receipt["scored_sessions"] == 5
    window = plan["windows"][0]
    assert plans.validate_fit(window, ["2020-01-02"], ["2020-01-06"])["rows"] == 1
    # A feature can be in the fit prefix while its eventual target is unavailable.
    with pytest.raises(ValueError, match="cutoff"):
        plans.validate_fit(window, ["2020-01-02"], ["2020-01-07"])
    with pytest.raises(ValueError, match="train"):
        plans.validate_research_dates(study, ["2020-01-06", "2020-01-16"])


@pytest.mark.parametrize("defect", ["gap", "test", "overlap", "calendar", "old_splits"])
def test_invalid_window_plan_cannot_drive_either_engine(chronology, defect):
    study, plan, sessions = deepcopy(chronology)
    if defect == "gap":
        plan["windows"][0]["gap_sessions"] = 2
    elif defect == "test":
        plan["windows"][1]["score"][1] = "2020-01-16"
    elif defect == "overlap":
        plan["windows"][1].update(fit=["2020-01-02", "2020-01-03"], score=["2020-01-09", "2020-01-15"])
    elif defect == "calendar":
        sessions.pop()
    else:
        study["splits"]["validation"] = ["2020-01-08", "2020-01-15"]
    with pytest.raises(ValueError):
        plans.validate_plan(study, plan, sessions)


def test_parallel_completion_exists_without_collect_and_observation_is_separate(tmp_path):
    ledger = records.TrialRecords(tmp_path)
    ledger.plan("unstarted", {"candidate": "proposal"})

    def run(i):
        return ledger.execute(f"T{i}", {"candidate": i},
                              lambda: {"payload": {"value": -i}, "outputs": []})

    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(run, range(8)))
    rows = ledger.reconcile(workers_stopped=True)
    assert sum(r["state"] == "computed" for r in rows) == 8
    assert not any(r["observed"] for r in rows)
    assert next(r for r in rows if r["trial_id"] == "unstarted")["state"] == "planned"
    assert ledger.observe("T3")["value"] == -3  # Losses remain completed results.
    assert sum(r["observed"] for r in ledger.reconcile()) == 1
    assert all(Path(r["result_path"]).exists() for r in receipts)
    cached = ledger.execute("T3", {"candidate": 3}, lambda: pytest.fail("must not rerun"))
    assert cached["cached"]
    with pytest.raises(ValueError, match="new trial"):
        ledger.execute("T3", {"candidate": 4}, lambda: None)


def test_crash_between_result_publication_and_completion_event_is_reconciled(tmp_path, monkeypatch):
    ledger = records.TrialRecords(tmp_path)
    original = ledger.event

    def crash(path, state, **details):
        if state == "computed":
            raise KeyboardInterrupt()
        return original(path, state, **details)

    monkeypatch.setattr(ledger, "event", crash)
    with pytest.raises(KeyboardInterrupt):
        ledger.execute("done", {}, lambda: {"payload": {"x": 1}, "outputs": []})
    monkeypatch.setattr(ledger, "event", original)
    ledger.plan("pending", {})
    with ledger.locked():
        ledger.event(ledger.folder("pending"), "dispatched")
    # Simulate a killed writer as well as a missing completion event.
    with (tmp_path / "pending/events.jsonl").open("ab") as stream:
        stream.write(b'{"partial":')
    rows = {r["trial_id"]: r for r in ledger.reconcile(workers_stopped=True)}
    assert rows["done"]["state"] == "computed" and not rows["done"]["observed"]
    assert rows["pending"]["state"] == "interrupted"
    assert len(list((tmp_path / "pending").glob("partial-event-*"))) == 1
    assert ledger.execute("pending", {}, lambda: {"payload": {}, "outputs": []})["cached"] is False


def test_changed_output_is_not_a_valid_completed_result(tmp_path):
    ledger = records.TrialRecords(tmp_path / "receipts")
    artifact = tmp_path / "curve.json"
    artifact.write_text('{"return": -0.2}')
    output = {"path": str(artifact), "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
    ledger.execute("trial", {}, lambda: {"payload": {"return": -0.2}, "outputs": [output]})
    artifact.write_text('{"return": 0.9}')
    assert ledger.reconcile()[0]["state"] == "invalid"
    with pytest.raises(ValueError, match="hash mismatch"):
        ledger.observe("trial")


def test_final_access_is_shared_immutable_and_remembers_prior_exposure(tmp_path):
    ledger = records.TrialRecords(tmp_path)
    bundle = {"candidate_sha256": "frozen candidate", "method": "agent chosen", "windows": ["a", "b"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: ledger.reserve_final(bundle, prior_exposure="previously_exposed"), range(2)))
    assert sorted(r["replay"] for r in receipts) == [False, True]
    assert all(r["prior_exposure"] == "previously_exposed" for r in receipts)
    with pytest.raises(ValueError, match="different bundle"):
        ledger.reserve_final({**bundle, "method": "changed after test"}, prior_exposure="previously_exposed")
    with pytest.raises(ValueError, match="different bundle"):
        ledger.reserve_final(bundle, prior_exposure="no_known_exposure")
    data = tmp_path / "download.json"
    data.write_text('{"synthetic": true}')
    first = ledger.bind_final_snapshot(bundle, data)
    assert ledger.bind_final_snapshot(bundle, data) == first
    data.write_text('{"synthetic": false}')
    with pytest.raises(ValueError, match="changed"):
        ledger.bind_final_snapshot(bundle, data)


def test_current_study_accepts_train_only_plan_and_retains_known_history():
    study = json.loads((SCRIPTS.parents[4] / "examples/tasks/factor_strategy_mining/signal_foundry/study.json").read_text())
    # The real config feeds a nonannual plan; no hidden three-fold/date assumptions.
    sessions = ["2022-10-03", "2022-10-04", "2022-10-05", "2022-10-06", "2022-10-07"]
    plan = {"schema": 1, "study_id": study["study_id"], "train_sessions_sha256": plans.digest(sessions),
            "windows": [{"id": "custom", "fit": sessions[:2], "score": sessions[3:], "gap_sessions": 1}]}
    result = plans.validate_plan(study, plan, sessions)
    assert result["test_prior_exposure"] == "previously_exposed"
