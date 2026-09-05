import json
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen, Request

import pytest

from agentevolver.visual.run.server import RunMonitor, RunView, handler, safe_url


@pytest.fixture
def monitor(tmp_path):
    return RunMonitor(tmp_path / "session" / "log", session_id="s")


def append(monitor, events, suffix="\n"):
    root = Path(monitor.state["log_root"]) / "trace"
    root.mkdir(exist_ok=True)
    path = root / "s.jsonl"
    with path.open("a") as stream:
        stream.write("\n".join(json.dumps(e) for e in events) + suffix)
    return path


def event(**kwargs):
    return dict(event_type="agent_call", session_id="s", task_id="p", agent_name="agent",
                timestamp="2026-09-05T00:00:00+00:00", **kwargs)


def test_incremental_usage_and_partial_lines(monitor):
    view = RunView(monitor.path)
    row = event(usage=dict(input_tokens=10, output_tokens=5, cache_read_tokens=90, cost=0.25))
    path = append(monitor, [row])
    assert view.snapshot()["usage"]["calls"] == 1
    assert view.snapshot()["usage"]["cost"] == .25
    assert view.snapshot()["usage"]["cache_hit_ratio"] == .9
    append(monitor, [row], suffix="")
    assert view.snapshot()["usage"]["calls"] == 1
    with path.open("a") as stream:
        stream.write("\n")
    assert view.snapshot()["usage"]["calls"] == 2
    path.write_text(json.dumps(row) + "\n")
    assert view.snapshot()["usage"]["calls"] == 1
    path.unlink()
    assert view.snapshot()["usage"]["calls"] == 0


def test_pid_reuse_and_terminal_state(monitor):
    assert RunView(monitor.path).snapshot()["alive"]
    monitor.state["launcher_start"] = "not-the-same-process"
    monitor.publish()
    assert RunView(monitor.path).snapshot()["status"] == "interrupted"
    monitor.publish(status="done")
    assert RunView(monitor.path).snapshot()["status"] == "done"


def test_runtime_idle_and_parent_relationships(monitor):
    monitor.publish([dict(pid="child", name="user", session="s", parent="builder", state="waiting", topics=["release"], turns=2)])
    row = RunView(monitor.path).snapshot()["agents"][0]
    assert row["parent"] == "builder"
    assert row["state"] == "waiting"
    assert row["usage"]["calls"] == 0


def test_dead_launcher_does_not_leave_busy_agents(monitor):
    monitor.state["launcher_start"] = "dead-process"
    monitor.publish([dict(pid="child", name="user", session="s", state="running", busy=True)])
    row = RunView(monitor.path).snapshot()["agents"][0]
    assert row["state"] == "interrupted"
    assert row["last_observed_state"] == "running"
    assert not row["busy"]


def test_no_prompts_or_tool_inputs_exposed(monitor):
    append(monitor, [event(input={"secret": "DO-NOT-EXPOSE"}, output="DO-NOT-EXPOSE", error="DO-NOT-EXPOSE")])
    data = RunView(monitor.path).snapshot()
    assert "DO-NOT-EXPOSE" not in json.dumps(data)
    assert data["usage"]["costed_calls"] == 0


def test_deployment_scope_and_safe_links(monitor, tmp_path):
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({
        "ours": {"site_id": "ours", "url": "http://localhost:3000", "deployed_at": "2026-09-05T02:00:00+00:00", "request": {"source_dir": monitor.state["workspace"]}},
        "another": {"site_id": "another", "url": "http://localhost:3001", "request": {"source_dir": str(tmp_path / "another")}},
    }))
    monitor.state["deploy_registry"] = str(sites)
    monitor.publish()
    assert [d["site_id"] for d in RunView(monitor.path).snapshot()["deployments"]] == ["ours"]
    assert RunView(monitor.path).snapshot()["deployments"][0]["deployed_at"] == "2026-09-05T02:00:00+00:00"
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("http://user:password@example.com/") is None


@pytest.mark.parametrize("gateway_base", [None, "http://localhost:8766", "https://stale.example"])
def test_product_and_history_links_always_use_monitor_origin(monitor, tmp_path, gateway_base):
    sites = tmp_path / "sites.json"
    sites.write_text(json.dumps({
        "echo": {"site_id": "echo", "url": "http://localhost:44901",
                 "versions": [{"number": 1}, {"number": 2}],
                 "request": {"source_dir": monitor.state["workspace"]}},
        "preview": {"site_id": "preview", "url": None,
                    "versions": [{"number": 1}],
                    "request": {"source_dir": monitor.state["workspace"]}},
    }))
    monitor.state.update(deploy_registry=str(sites), gateway_base=gateway_base)
    monitor.publish()
    view = RunView(monitor.path)
    for _ in range(2):
        rows = view.snapshot()["deployments"]
        assert rows[0]["url"] == "/s/echo/"
        assert [v["url"] for v in rows[0]["versions"]] == ["/s/echo--r1/", "/s/echo--r2/"]
        assert rows[1]["url"] is None
        assert rows[1]["versions"][0]["url"] == "/s/preview--r1/"
        assert "44901" not in json.dumps(rows)
        # An already-running launcher does not know fields added by a repair worker.
        monitor.state.pop("gateway_base", None)
        monitor.publish()


def test_request_allowlist_and_symlinks(monitor, tmp_path):
    root = Path(monitor.state["log_root"]) / "model_requests" / "agent"
    root.mkdir(parents=True)
    # Persisted requests from before the asset migration must still open.
    (root / "one.html").write_text('<link href="../../visual/css/request.css"><script src="../../visual/js/request.js"></script>')
    view = RunView(monitor.path)
    assert b'href="/request.css"' in view.request_html("agent/one.html")
    (root / "new.html").write_text('<link href="../../visual/request/style.css"><script src="../../visual/request/app.js"></script>')
    rendered = view.request_html("agent/new.html", "/s/monitor/")
    assert b'href="/s/monitor/request.css"' in rendered
    assert b'src="/s/monitor/request.js"' in rendered
    private = tmp_path / "private.html"
    private.write_text("secret")
    (root / "escape.html").symlink_to(private)
    for name in ("agent/escape.html", "../../private.html", str(private), "agent/.env"):
        with pytest.raises(ValueError):
            view.request_html(name)


def test_http_read_only_and_security_headers(monitor, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(RunView(monitor.path), tmp_path))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(url + "/api/status") as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert json.load(response)["session_id"] == "s"
        for request, code in ((url + "/.env", 404), (Request(url + "/api/status", method="POST", data=b"{}"), 501)):
            with pytest.raises(HTTPError) as error:
                urlopen(request)
            assert error.value.code == code
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.asyncio
async def test_deploy_reuses_shared_palette_and_framework(monitor, monkeypatch):
    from agentevolver.deploy import SiteRecord, SiteStatus
    captured = {}

    async def gateway():
        monkeypatch.setenv("GATEWAY_PUBLIC_BASE", "http://localhost:9876")
        return "http://localhost:9876"

    monkeypatch.setattr("agentevolver.gateway.sites.ensure_site_gateway", gateway)

    async def deploy(request):
        captured["request"] = request
        return SiteRecord(site_id=request.site_id, runtime="custom", status=SiteStatus.RUNNING, url="http://localhost:8766")

    monkeypatch.setattr("agentevolver.deploy.deployment_manager.deploy", deploy)
    assert (await monitor.deploy()).startswith("http://localhost:9876/s/run-monitor-")
    request = captured["request"]
    assert request.backend == "host"
    assert "--state" in request.overrides["start"]
    from agentevolver.paths import path_manager
    assert request.files["base.css"] == path_manager.package_resource("visual", "benchmark", "style.css").read_text()
    assert '"127.0.0.1"' in request.files["run_server.py"]
