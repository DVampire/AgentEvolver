"""Generic live view for benchmark runs.

Launchers publish a small, provider-neutral state file.  The HTTP process reads that
file plus the launcher's result ledger; it never imports a benchmark implementation or
guesses state from process command lines.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
STATE_FILE = "monitor.json"
_STEP_RE = re.compile(r"step\s+(\d+)/(\d+)", re.IGNORECASE)
_SAFE_ID = re.compile(r"[^a-z0-9-]+")
_TRACE_LOCK = threading.Lock()
_TRACE_CACHE: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value: Any) -> None:
    """Atomically publish a complete snapshot for lock-free HTTP readers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _process_start(pid: int) -> int | None:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _elapsed(started_at: str | None) -> int | None:
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    except ValueError:
        return None


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("records", "results", "tasks"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def _number(value: Any, cast: type[int] | type[float]) -> int | float:
    try:
        return cast(value or 0)
    except (TypeError, ValueError):
        return cast(0)


def _empty_usage() -> dict[str, int | float]:
    return {
        "cost_usd": 0.0,
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


def _add_usage(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        target[key] += int(_number(source.get(key), int))
    target["cost_usd"] += float(_number(source.get("cost_usd"), float))


def _trace_usage(path: Path) -> dict[str, int | float]:
    """Incrementally sum model usage from one append-only trace."""
    try:
        stat = path.stat()
    except OSError:
        return _empty_usage()
    identity = (stat.st_dev, stat.st_ino)
    key = str(path)
    with _TRACE_LOCK:
        cached = _TRACE_CACHE.get(key)
        if not cached or cached["identity"] != identity or stat.st_size < cached["offset"]:
            cached = {"identity": identity, "offset": 0, "usage": _empty_usage()}
        try:
            with path.open("rb") as stream:
                stream.seek(cached["offset"])
                while True:
                    position = stream.tell()
                    line = stream.readline()
                    if not line:
                        break
                    # The writer may be between write() calls. Re-read an incomplete JSON
                    # object on the next refresh rather than treating it as corrupt forever.
                    if not line.endswith(b"\n"):
                        stream.seek(position)
                        break
                    try:
                        event = json.loads(line)
                    except (UnicodeDecodeError, ValueError):
                        continue
                    if event.get("event_type") != "agent_call":
                        continue
                    usage = event.get("usage") or {}
                    _add_usage(cached["usage"], {
                        "calls": 1,
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "cache_read_tokens": usage.get("cache_read_tokens"),
                        "cache_write_tokens": usage.get("cache_write_tokens"),
                        "cost_usd": usage.get("cost"),
                    })
                cached["offset"] = stream.tell()
        except OSError:
            pass
        _TRACE_CACHE[key] = cached
        return dict(cached["usage"])


def _live_usage(log_dir: Path) -> dict[str, int | float]:
    usage = _empty_usage()
    trace_dir = log_dir / "trace"
    try:
        traces = list(trace_dir.glob("*.jsonl")) if trace_dir.is_dir() else []
    except OSError:
        traces = []
    for trace in traces:
        _add_usage(usage, _trace_usage(trace))
    return usage


def _outcome(record: dict[str, Any]) -> str:
    grade = record.get("final_grade")
    if isinstance(grade, dict) and grade.get("error_code"):
        return "error"
    if record.get("error") and record.get("status") not in {"done", "completed"}:
        return "error"
    if record.get("resolved") is not None:
        return "passed" if record.get("resolved") is True else "failed"
    if record.get("score") is not None:
        try:
            return "passed" if float(record["score"]) > 0 else "failed"
        except (TypeError, ValueError):
            pass
    return "completed" if record.get("status") in {"done", "completed"} else "error"


def _activity(owner_dir: Path | None, task: dict[str, Any]) -> dict[str, Any]:
    result = dict(task)
    result["elapsed_seconds"] = _elapsed(task.get("started_at"))
    if owner_dir is None:
        return result
    sessions = (owner_dir / "sessions").resolve()
    log_dir = (sessions / str(task.get("task_id")) / "log").resolve()
    try:
        log_dir.relative_to(sessions)
    except ValueError:
        return result
    agent_log = log_dir / "agent.log"
    steps: list[tuple[str, str]] = []
    try:
        with agent_log.open("rb") as stream:
            stream.seek(max(0, agent_log.stat().st_size - 512_000))
            steps = _STEP_RE.findall(stream.read().decode("utf-8", "replace"))
    except OSError:
        pass
    try:
        requests = (
            list((log_dir / "model_requests").rglob("*.html"))
            if (log_dir / "model_requests").is_dir() else []
        )
    except OSError:
        requests = []
    paths = [path for path in (agent_log, *requests) if path.exists()]
    latest = max((path.stat().st_mtime for path in paths), default=None)
    if steps:
        result["step"], result["max_step"] = map(int, steps[-1])
    result["requests"] = len(requests)
    result["last_activity_seconds"] = max(0, int(time.time() - latest)) if latest else None
    result["usage"] = _live_usage(log_dir)
    return result


def build_snapshot(state_path: str | Path) -> dict[str, Any]:
    """Build the stable JSON consumed by the browser."""
    state_path = Path(state_path).resolve()
    state = _read_json(state_path, {})
    results_path = Path(state.get("results_path") or state_path.with_name("results.json"))
    records = _records(_read_json(results_path, []))
    outcomes = [_outcome(record) for record in records]
    telemetry = _empty_usage()
    recent = []
    for record, outcome in zip(records, outcomes):
        spend = record.get("spend") or {}
        _add_usage(telemetry, {
            "cost_usd": spend.get("total_cost_usd"),
            "calls": spend.get("n_llm_calls"),
            "input_tokens": spend.get("input_tokens"),
            "output_tokens": spend.get("output_tokens"),
            "cache_read_tokens": spend.get("cache_read_tokens"),
            "cache_write_tokens": spend.get("cache_write_tokens"),
        })
        recent.append({
            "task_id": record.get("instance_id") or record.get("task_id") or "unknown",
            "outcome": outcome,
            "time_seconds": record.get("time_seconds", record.get("time")),
            "calls": int(_number(spend.get("n_llm_calls"), int)),
            "cost_usd": float(_number(spend.get("total_cost_usd"), float)),
        })

    owner = Path(state["owner_dir"]) if state.get("owner_dir") else None
    active = [_activity(owner, task) for task in (state.get("active") or {}).values()]
    active.sort(key=lambda item: (item.get("position", 0), item.get("task_id", "")))
    completed_ids = {
        str(record.get("instance_id") or record.get("task_id"))
        for record in records
        if record.get("instance_id") is not None or record.get("task_id") is not None
    }
    for task in active:
        # Result publication and active-row removal are separate atomic writes. Avoid a
        # transient double count when an HTTP refresh lands between those two operations.
        if str(task.get("task_id")) not in completed_ids:
            _add_usage(telemetry, task.get("usage") or {})
    cache_base = telemetry["input_tokens"] + telemetry["cache_read_tokens"]
    telemetry["cache_hit_percent"] = (
        100 * telemetry["cache_read_tokens"] / cache_base if cache_base else 0.0
    )
    pid = int(state.get("launcher_pid") or 0)
    alive = bool(pid and _process_start(pid) == state.get("launcher_start"))
    status = state.get("status", "unknown")
    if status == "running" and not alive:
        status = "interrupted"
    passed = outcomes.count("passed")
    failed = outcomes.count("failed")
    errors = outcomes.count("error")
    scored = passed + failed
    total = int(state.get("total") or len(records))
    elapsed = _elapsed(state.get("started_at"))
    throughput = max(0, len(records) - int(state.get("initial_completed") or 0))
    eta = int(elapsed / throughput * (total - len(records))) if elapsed and throughput else None
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _now(),
        "benchmark": state.get("benchmark", "benchmark"),
        "title": state.get("title") or state.get("benchmark", "Benchmark"),
        "run_id": state.get("run_id") or state_path.parent.name,
        "status": status,
        "results_path": str(results_path),
        "progress": {
            "completed": len(records), "total": total, "scored": scored,
            "passed": passed, "failed": failed, "errors": errors,
        },
        "launcher": {
            "alive": alive, "pid": pid or None, "elapsed_seconds": elapsed,
            "concurrency": int(state.get("concurrency") or 1), "active": active,
        },
        "telemetry": telemetry,
        "eta_seconds": eta,
        "recent": list(reversed(recent[-10:])),
        "monitor_url": state.get("monitor_url"),
    }


class BenchmarkMonitor:
    """Small state publisher shared by every benchmark launcher."""

    def __init__(
        self, run_dir: str, benchmark: str, total: int, concurrency: int,
        *, results_path: str | None = None, owner_dir: str | None = None,
        title: str | None = None,
    ) -> None:
        from agentevolver.paths import path_manager

        self.run_dir = Path(run_dir).resolve()
        self.path = Path(path_manager.resolve_under(self.run_dir, STATE_FILE))
        existing = _read_json(self.path, {})
        self.state = {
            "schema_version": SCHEMA_VERSION,
            "benchmark": benchmark,
            "title": title or benchmark.replace("_", " ").title(),
            "run_id": self.run_dir.name,
            "results_path": str(Path(results_path or self.run_dir / "results.json").resolve()),
            "owner_dir": str(Path(owner_dir).resolve()) if owner_dir else None,
            "total": int(total),
            "concurrency": int(concurrency),
            "status": "running",
            # A resumed launcher is a new timed execution window: completed records remain
            # progress, while elapsed time and ETA must not include time spent offline.
            "started_at": _now(),
            "updated_at": _now(),
            "launcher_pid": os.getpid(),
            "launcher_start": _process_start(os.getpid()),
            "initial_completed": len(
                _records(_read_json(Path(results_path or self.run_dir / "results.json"), []))
            ),
            # A new launcher process owns a new live set. Completed/interrupted state may
            # be resumed, but stale worker rows from the dead process may not.
            "active": {},
            "monitor_site_id": existing.get("monitor_site_id"),
            "monitor_url": existing.get("monitor_url"),
        }
        self.publish()

    def publish(self) -> None:
        self.state["updated_at"] = _now()
        _write_json(self.path, self.state)

    def task(self, task_id: str, phase: str, *, position: int | None = None) -> None:
        current = self.state["active"].get(task_id, {})
        self.state["active"][task_id] = {
            "task_id": task_id,
            "phase": phase,
            "position": position if position is not None else current.get("position", 0),
            "started_at": current.get("started_at") or _now(),
            "updated_at": _now(),
        }
        self.publish()

    def finish_task(self, task_id: str) -> None:
        self.state["active"].pop(task_id, None)
        self.publish()

    def close(self, status: str = "completed") -> None:
        self.state["status"] = status
        self.state["active"] = {}
        self.state["finished_at"] = _now()
        self.publish()

    async def deploy(self, port: int = 8765) -> str | None:
        """Deploy this view through the framework's normal service lifecycle."""
        from agentevolver.deploy import DeployRequest, deployment_manager

        slug = _SAFE_ID.sub("-", self.state["benchmark"].lower()).strip("-") or "benchmark"
        digest = hashlib.sha256(str(self.run_dir).encode()).hexdigest()[:10]
        site_id = f"benchmark-{slug}-{digest}"
        source = Path(__file__).resolve().parent
        files = {
            "benchmark.py": Path(__file__).read_text(encoding="utf-8"),
            "index.html": (source / "index.html").read_text(encoding="utf-8"),
            "benchmark.css": (source / "style.css").read_text(encoding="utf-8"),
            "benchmark.js": (source / "app.js").read_text(encoding="utf-8"),
        }
        request = DeployRequest(
            site_id=site_id,
            runtime="custom",
            backend="host",
            port=port,
            files=files,
            overrides={
                "start": f"python3 benchmark.py serve --state {shlex.quote(str(self.path))} "
                "--host 0.0.0.0 --port $PORT",
                "health": {"type": "http", "path": "/api/status", "timeout_s": 20},
            },
        )
        record = await deployment_manager.deploy(request)
        status = getattr(record.status, "value", record.status)
        if status != "running":
            return None
        self.state["monitor_site_id"] = site_id
        self.state["monitor_url"] = record.url
        self.publish()
        return record.url


def _handler(state_path: Path, asset_dir: Path) -> type[BaseHTTPRequestHandler]:
    assets = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/benchmark.css": ("benchmark.css", "text/css; charset=utf-8"),
        "/benchmark.js": ("benchmark.js", "text/javascript; charset=utf-8"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/api/status":
                payload = json.dumps(build_snapshot(state_path), ensure_ascii=False).encode()
                return self.send_payload(payload, "application/json; charset=utf-8", True)
            asset = assets.get(path)
            if not asset:
                return self.send_error(404)
            try:
                payload = (asset_dir / asset[0]).read_bytes()
            except OSError:
                return self.send_error(404)
            self.send_payload(payload, asset[1], False)

        def send_payload(self, payload: bytes, content_type: str, no_cache: bool) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store" if no_cache else "public, max-age=60")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

    return Handler


def serve(state: str, host: str, port: int) -> None:
    asset_dir = Path(__file__).resolve().parent
    server = ThreadingHTTPServer((host, port), _handler(Path(state), asset_dir))
    print(f"Benchmark monitor: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("serve")
    command.add_argument("--state", required=True)
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.state, args.host, args.port)


if __name__ == "__main__":
    main()
