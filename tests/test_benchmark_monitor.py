"""The generic benchmark view is driven only by its state and result contracts."""

import json
from pathlib import Path

import pytest

from agentevolver.deploy.types import SiteRecord, SiteStatus
from agentevolver.visual import BenchmarkMonitor, build_snapshot


def test_dashboard_uses_the_shared_visual_palette():
    css = (Path(__file__).parents[1] / "agentevolver/visual/benchmark/style.css").read_text()

    for token in ("#0E1210", "#141B12", "#C8DDB8", "#5BAD78", "#CFA040", "#D45E3E"):
        assert token in css


def test_monitor_publishes_progress_and_live_activity(tmp_path):
    run = tmp_path / "run"
    owner = tmp_path / "owner"
    results = run / "results.json"
    results.parent.mkdir(parents=True)
    results.write_text(json.dumps([
        {
            "instance_id": "instance_passed",
            "status": "done",
            "resolved": True,
            "spend": {"n_llm_calls": 2, "input_tokens": 10, "cache_read_tokens": 90,
                      "output_tokens": 4, "total_cost_usd": 0.5},
        },
        {"instance_id": "instance_failed", "status": "done", "resolved": False},
        {"instance_id": "instance_error", "status": "failed", "error": "pull failed"},
    ]), encoding="utf-8")

    log = owner / "sessions" / "instance_live" / "log"
    (log / "model_requests" / "agent").mkdir(parents=True)
    (log / "agent.log").write_text("step 3/20\n", encoding="utf-8")
    (log / "model_requests" / "agent" / "0001.html").write_text("request", encoding="utf-8")
    (log / "trace").mkdir()
    trace = log / "trace" / "instance_live.jsonl"
    trace.write_text(json.dumps({
        "event_type": "agent_call",
        "usage": {"input_tokens": 5, "output_tokens": 6, "cache_read_tokens": 45,
                  "cache_write_tokens": 7, "cost": 0.25},
    }) + "\n", encoding="utf-8")

    monitor = BenchmarkMonitor(
        str(run), "demo", 5, 2, results_path=str(results), owner_dir=str(owner)
    )
    monitor.task("instance_live", "solving", position=1)
    snapshot = build_snapshot(monitor.path)

    assert snapshot["progress"] == {
        "completed": 3, "total": 5, "scored": 2,
        "passed": 1, "failed": 1, "errors": 1,
    }
    assert snapshot["launcher"]["active"][0]["step"] == 3
    assert snapshot["launcher"]["active"][0]["requests"] == 1
    assert snapshot["telemetry"]["calls"] == 3
    assert snapshot["telemetry"]["cost_usd"] == 0.75
    assert snapshot["telemetry"]["cache_hit_percent"] == 90.0

    # The trace reader is incremental: appended calls appear once, not once per refresh.
    with trace.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "event_type": "agent_call",
            "usage": {"input_tokens": 2, "output_tokens": 3, "cache_read_tokens": 18,
                      "cache_write_tokens": 0, "cost": 0.1},
        }) + "\n")
    snapshot = build_snapshot(monitor.path)
    assert snapshot["telemetry"]["calls"] == 4
    assert snapshot["telemetry"]["cost_usd"] == pytest.approx(0.85)
    assert build_snapshot(monitor.path)["telemetry"]["calls"] == 4

    monitor.finish_task("instance_live")
    monitor.close()
    assert build_snapshot(monitor.path)["status"] == "completed"


def test_monitor_reads_wrapped_result_ledgers(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    results = run / "program.json"
    results.write_text(json.dumps({
        "records": [
            {"instance_id": "done", "status": "done"},
            {"instance_id": "bad", "status": "failed", "error": "boom"},
        ]
    }), encoding="utf-8")
    monitor = BenchmarkMonitor(str(run), "programbench", 2, 1, results_path=str(results))

    progress = build_snapshot(monitor.path)["progress"]
    assert progress["completed"] == 2
    assert progress["scored"] == 0
    assert progress["errors"] == 1


def test_resumed_monitor_uses_current_results_as_eta_baseline(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    results = run / "results.json"
    results.write_text(json.dumps([{"task_id": "old", "status": "done"}]), encoding="utf-8")
    first = BenchmarkMonitor(str(run), "demo", 3, 1, results_path=str(results))
    first.close("interrupted")

    resumed = BenchmarkMonitor(str(run), "demo", 3, 1, results_path=str(results))
    state = json.loads(resumed.path.read_text())

    assert state["initial_completed"] == 1
    assert state["status"] == "running"
    assert state["active"] == {}


@pytest.mark.asyncio
async def test_monitor_deploys_through_deployment_manager(monkeypatch, tmp_path):
    captured = {}

    async def deploy(request):
        captured["request"] = request
        return SiteRecord(
            site_id=request.site_id,
            runtime=request.runtime,
            status=SiteStatus.RUNNING,
            url="http://localhost:9000",
        )

    monkeypatch.setattr("agentevolver.deploy.deployment_manager.deploy", deploy)
    monitor = BenchmarkMonitor(str(tmp_path / "run"), "demo", 1, 1)

    assert await monitor.deploy(9000) == "http://localhost:9000"
    request = captured["request"]
    assert request.backend == "host"
    assert request.runtime == "custom"
    assert {"benchmark.py", "index.html", "benchmark.css", "benchmark.js"} == set(request.files)
    assert json.loads(Path(monitor.path).read_text())["monitor_url"] == "http://localhost:9000"
