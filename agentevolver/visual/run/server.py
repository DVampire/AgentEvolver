"""Run dashboard. The deployed reader is stdlib-only and never invokes an agent.

Runtime snapshots supply lifecycle facts; incremental traces supply activity and
usage. Attaching to an older launcher works too, with explicitly trace-only state.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import threading
from urllib.parse import quote, unquote, urlsplit


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def process_start(pid):
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, ValueError, IndexError, TypeError):
        return None


def now():
    return datetime.now(timezone.utc).isoformat()


def usage():
    return dict(calls=0, input_tokens=0, output_tokens=0, cache_read_tokens=0,
                cache_write_tokens=0, cost=0, costed_calls=0)


def safe_url(value):
    try:
        url = urlsplit(str(value or ""))
        return str(value) if url.scheme in {"http", "https"} and url.hostname and not url.username and not url.password else None
    except ValueError:
        return None


class TraceReader:
    """Keep aggregates, not raw prompts; consume only complete appended lines."""

    def __init__(self):
        self.files = {}

    def update(self, root):
        paths = {p for p in root.glob("*.jsonl") if p.resolve().is_relative_to(root.resolve())}
        for gone in self.files.keys() - paths:
            del self.files[gone]
        for path in paths:
            stat = path.stat()
            item = self.files.get(path)
            if not item or item["inode"] != stat.st_ino or stat.st_size < item["offset"]:
                item = dict(inode=stat.st_ino, offset=0, agents={}, events=deque(maxlen=100), sites=set())
                self.files[path] = item
            with path.open("rb") as stream:
                stream.seek(item["offset"])
                for line in stream:
                    if not line.endswith(b"\n"):
                        break
                    item["offset"] += len(line)
                    try:
                        self.consume(item, json.loads(line))
                    except (ValueError, TypeError, AttributeError):
                        continue
        return list(self.files.values())

    @staticmethod
    def consume(item, event):
        name = event.get("agent_name") or "unknown"
        key = (event.get("session_id") or "") + ":" + (event.get("task_id") or name)
        row = item["agents"].setdefault(key, dict(
            id=key, name=name, session=event.get("session_id"), pid=event.get("task_id"),
            phase="observed", step=None, updated_at="", usage=usage(), requests=0))
        typ = event.get("event_type")
        if (event.get("timestamp") or "") >= row["updated_at"]:
            row["updated_at"] = event.get("timestamp") or ""
            row["step"] = event.get("step_number") if event.get("step_number") is not None else row["step"]
            row["action"] = event.get("action_name") or typ
            row["phase"] = {
                "agent_start": "running", "model_request": "awaiting model",
                "agent_call": "model returned", "tool_start": "executing tool",
                "tool_call": "tool returned", "agent_end": "turn ended", "error": "error",
            }.get(typ, row["phase"])
        if typ == "model_request":
            row["requests"] += 1
        if typ == "agent_call":
            values = event.get("usage") or {}
            row["usage"]["calls"] += 1
            row["usage"]["costed_calls"] += int(isinstance(values.get("cost"), (int, float)))
            for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "cost"):
                row["usage"][field] += values.get(field) or 0
        # Only names and outcomes, never tool arguments, prompts, or provider state.
        item["events"].append(dict(timestamp=event.get("timestamp"), agent=name, type=typ,
                                   action=event.get("action_name"), success=event.get("success")))
        if typ == "tool_start" and event.get("action_name") == "deploy_tool":
            site = (event.get("input") or {}).get("site_id")
            if isinstance(site, str):
                item["sites"].add(site)


class RunView:
    def __init__(self, state_path):
        self.path = Path(state_path)
        self.reader = TraceReader()
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            state = read_json(self.path, {})
            if not state.get("log_root"):
                raise ValueError("Run state is unavailable")
            log = Path(state["log_root"])
            chunks = self.reader.update(log / "trace")
            agents = {}
            events = []
            sites = set()
            for chunk in chunks:
                events.extend(chunk["events"])
                sites.update(chunk["sites"])
                for key, row in chunk["agents"].items():
                    if key not in agents:
                        agents[key] = {**row, "usage": dict(row["usage"])}
                    else:
                        previous = agents[key]
                        totals = {k: previous["usage"][k] + row["usage"][k] for k in usage()}
                        count = previous["requests"] + row["requests"]
                        agents[key] = {**max((previous, row), key=lambda a: a["updated_at"]), "usage": totals, "requests": count}
            for proc in state.get("processes", []):
                key = str(proc.get("session") or "") + ":" + str(proc.get("pid"))
                row = agents.setdefault(key, dict(id=key, name=proc.get("name"), usage=usage(), requests=0))
                row.update({k: proc.get(k) for k in ("pid", "parent", "state", "mode", "turns", "busy", "queued", "topics", "grants")})
            total = {key: sum(a["usage"][key] for a in agents.values()) for key in usage()}
            inputs = total["input_tokens"] + total["cache_read_tokens"] + total["cache_write_tokens"]
            total["cache_hit_ratio"] = total["cache_read_tokens"] / inputs if inputs else None
            alive = bool(state.get("launcher_start") and process_start(state.get("launcher_pid")) == state["launcher_start"])
            status = state.get("status", "unknown")
            if status == "running" and not alive:
                status = "interrupted"
            if not alive and state.get("launcher_start"):
                for row in agents.values():
                    if row.get("state") not in {"exited", "failed", "cancelled", "completed", "interrupted"}:
                        row["last_observed_state"] = row.get("state")
                        row["state"] = "interrupted"
                    row["busy"] = False
            registry = read_json(state.get("deploy_registry", ""), {})
            deployments = []
            for site_id, record in registry.items():
                source = (record.get("request") or {}).get("source_dir")
                belongs = source and Path(source).resolve().is_relative_to(Path(state["workspace"]).resolve())
                if site_id not in sites and not belongs:
                    continue
                base = state.get("gateway_base")
                public = f"{base}/s/{quote(site_id, safe='')}/" if base and record.get("url") else record.get("url")
                versions = [{"number": v.get("number"), "deployed_at": v.get("deployed_at"),
                             "source_revision": v.get("source_revision"),
                             "url": safe_url(f"{base}/s/{quote(site_id, safe='')}--r{int(v['number'])}/") if base else None}
                            for v in record.get("versions", []) if isinstance(v.get("number"), int)]
                deployments.append({**{k: record.get(k) for k in ("site_id", "status", "release_number", "source_revision", "created_at", "deployed_at", "updated_at")}, "url": safe_url(public), "versions": versions})
            manifest = read_json(state.get("extension_manifest", ""), {})
            components = [{k: c.get(k) for k in ("module", "name", "version")} for c in manifest.get("components", [])]
            requests = sorted((log / "model_requests").glob("*/*.html"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
            return dict(schema_version=1, title=state.get("title", "Agent run"), session_id=state.get("session_id"),
                        status=status, alive=alive, launcher_pid=state.get("launcher_pid"), started_at=state.get("started_at"),
                        updated_at=now(), runtime_updated_at=state.get("updated_at"),
                        observation="runtime + trace" if "processes" in state else "trace only",
                        agents=list(agents.values()), usage=total, deployments=deployments, components=components,
                        events=sorted(events, key=lambda e: e.get("timestamp") or "", reverse=True)[:100],
                        requests=[dict(name=p.name, agent=p.parent.name, url="request/" + quote(str(p.relative_to(log / "model_requests")))) for p in requests])

    def request_html(self, relative, prefix="/"):
        log = Path(read_json(self.path, {})["log_root"]).resolve()
        root = log / "model_requests"
        path = (root / relative).resolve()
        if not root.resolve().is_relative_to(log) or not path.is_relative_to(root.resolve()) or path.suffix != ".html" or len(path.relative_to(root.resolve()).parts) != 2:
            raise ValueError("Invalid request path")
        html = path.read_text(encoding="utf-8")
        # Old snapshots keep their content; normalize both asset layouts on serving.
        html = re.sub(r'href="[^"]*/(?:request/style\.css|request\.css)"', f'href="{prefix}request.css"', html)
        return re.sub(r'src="[^"]*/(?:request/app\.js|request\.js)"', f'src="{prefix}request.js"', html).encode()


class RunMonitor:
    """Launcher-owned small metadata file; model data stays in the trace store."""

    def __init__(self, log_root, *, session_id, title="Agent run", pid=None, extension_root=None):
        from agentevolver.paths import P, path_manager

        log = Path(log_root).resolve()
        self.path = path_manager.under(log, P.LOG_RUN_MONITOR)
        pid = os.getpid() if pid is None else pid
        self.state = dict(schema_version=1, title=title, session_id=session_id, log_root=str(log),
                          workspace=str(path_manager.resolve_under(log.parent, "workspace")),
                          status="running", started_at=now(), launcher_pid=pid, launcher_start=process_start(pid),
                          deploy_registry=str(path_manager.resolve_under(path_manager.get(P.DEPLOY), "sites.json")),
                          extension_manifest=str(path_manager.resolve_under(extension_root, "manifest.json")) if extension_root else None)
        self.publish()

    def publish(self, processes=None, status=None):
        if processes is not None:
            fields = ("pid", "name", "session", "parent", "state", "mode", "turns", "busy", "queued", "topics", "grants")
            self.state["processes"] = [{k: p.get(k) for k in fields} for p in processes]
        if status:
            self.state["status"] = status
        self.state["updated_at"] = now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".run-monitor-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.state, stream)
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def deploy(self, port=8766):
        from agentevolver.deploy import DeployRequest, deployment_manager
        from agentevolver.paths import path_manager
        from agentevolver.gateway.sites import ensure_site_gateway

        self.state["gateway_base"] = await ensure_site_gateway()
        self.publish()
        assets = {"run_server.py": ("run", "server.py"), "index.html": ("run", "index.html"),
                  "base.css": ("benchmark", "style.css"), "run.css": ("run", "style.css"),
                  "run.js": ("run", "app.js"), "request.css": ("request", "style.css"), "request.js": ("request", "app.js")}
        files = {name: path_manager.package_resource("visual", *parts).read_text() for name, parts in assets.items()}
        site_id = "run-monitor-" + hashlib.sha256(str(self.path).encode()).hexdigest()[:12]
        record = await deployment_manager.deploy(DeployRequest(
            site_id=site_id, runtime="custom", backend="host", port=port, files=files,
            title=self.state["title"], kind="run",
            overrides={"start": f"python3 run_server.py serve --state {shlex.quote(str(self.path))} --port $PORT",
                       "health": {"type": "http", "path": "/api/status", "timeout_s": 20}}))
        if getattr(record.status, "value", record.status) != "running":
            raise RuntimeError("Run monitor deployment failed")
        self.state["monitor_url"] = deployment_manager.public_urls(record)["site_url"]
        self.state["monitor_backend_url"] = record.url
        self.publish()
        return self.state["monitor_url"]

    async def start(self, port=8766):
        """Deploy outside the agent's cleanup scope, so finished runs stay viewable."""
        worker = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "agentevolver.visual.run.server", "deploy",
            "--state", str(self.path), "--port", str(port),
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(worker.communicate(), timeout=45)
        except (asyncio.CancelledError, TimeoutError):
            if worker.returncode is None:
                worker.terminate()
            await worker.communicate()
            raise
        updated = read_json(self.path, {})
        if worker.returncode or not updated.get("monitor_url"):
            raise RuntimeError("Run monitor could not start; check deployment logs")
        self.state.update(updated)
        return self.state["monitor_url"]


def handler(view, assets):
    allowed = {"/": ("index.html", "text/html"), "/base.css": ("base.css", "text/css"),
               "/run.css": ("run.css", "text/css"), "/run.js": ("run.js", "text/javascript"),
               "/request.css": ("request.css", "text/css"), "/request.js": ("request.js", "text/javascript")}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = unquote(urlsplit(self.path).path)
            try:
                if route == "/api/status":
                    payload, mime = json.dumps(view.snapshot()).encode(), "application/json"
                elif route.startswith("/request/"):
                    prefix = self.headers.get("X-Forwarded-Prefix", "/")
                    if not re.fullmatch(r"/(?:s/[A-Za-z0-9_.%~-]+/)?", prefix):
                        return self.send_error(400)
                    payload, mime = view.request_html(route.removeprefix("/request/"), prefix), "text/html"
                elif route in allowed:
                    name, mime = allowed[route]
                    payload = (assets / name).read_bytes()
                else:
                    return self.send_error(404)
            except (OSError, ValueError, KeyError):
                return self.send_error(503 if route == "/api/status" else 404)
            self.send_response(200)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            # Request HTML may contain untrusted model/tool output. It never gains
            # the dashboard origin, and cannot navigate, submit forms, or call APIs.
            policy = "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            if route.startswith("/request/"):
                policy = "sandbox allow-scripts; default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src data:; connect-src 'none'; base-uri 'none'"
            self.send_header("Content-Security-Policy", policy)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--state", required=True)
    serve.add_argument("--port", type=int, default=8766)
    deploy = sub.add_parser("deploy")
    deploy.add_argument("--state", required=True)
    deploy.add_argument("--port", type=int, default=8766)
    attach = sub.add_parser("attach")
    attach.add_argument("--log-root", required=True)
    attach.add_argument("--session-id", required=True)
    attach.add_argument("--pid", type=int, required=True)
    attach.add_argument("--title", default="Agent run")
    attach.add_argument("--extension-root")
    attach.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.command == "serve":
        # Read-only does not mean public: access through SSH/VS Code forwarding.
        ThreadingHTTPServer(("127.0.0.1", args.port), handler(RunView(args.state), Path(__file__).parent)).serve_forever()
    elif args.command == "deploy":
        monitor = RunMonitor.__new__(RunMonitor)
        monitor.path = Path(args.state).resolve()
        monitor.state = read_json(monitor.path, {})
        if not monitor.state.get("log_root"):
            parser.error("Invalid monitor state")
        print(asyncio.run(monitor.deploy(args.port)))
    else:
        if not process_start(args.pid):
            parser.error("Target process is not alive")
        monitor = RunMonitor(args.log_root, session_id=args.session_id, title=args.title, pid=args.pid, extension_root=args.extension_root)
        print(asyncio.run(monitor.deploy(args.port)))


if __name__ == "__main__":
    main()
