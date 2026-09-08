"""Shared usage queries. This module also runs in the stdlib-only deployed reader."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

if __package__:
    from agentevolver.trace.usage import UsageTraceReader, normalize_usage, number
else:
    from usage_trace import UsageTraceReader, normalize_usage, number


def deployment_files():
    """Package the same implementation for both standalone dashboard servers."""
    from agentevolver.paths import path_manager

    return {
        name: path_manager.package_resource(*parts).read_text(encoding="utf-8")
        for name, parts in {
            "usage_server.py": ("visual", "usage", "server.py"),
            "usage_trace.py": ("trace", "usage.py"),
            "usage.js": ("visual", "usage", "app.js"),
            "usage.css": ("visual", "usage", "style.css"),
        }.items()
    }


def stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


TOKEN_SERIES = (
    "input_tokens",
    "output_tokens",
    "cache_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "context_input_tokens",
    "unclassified_input",
    "reasoning_tokens",
)


def token_values(row):
    """Chart dimensions keep source missingness and do not add overlapping totals."""
    values = {key: row.get(key) for key in TOKEN_SERIES}
    read, write = values["cache_read_tokens"], values["cache_write_tokens"]
    values["cache_tokens"] = read + write if read is not None and write is not None else None
    context = values["context_input_tokens"]
    known_input = sum(
        values[k] or 0 for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens")
    )
    conflict = context is not None and known_input > context
    values["unclassified_input"] = (
        context - known_input if context is not None and not conflict else None
    )
    return values, conflict


def total(rows):
    fields = (
        "input_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "context_input_tokens",
        "output_tokens",
        "total_tokens",
    )
    result = {k: sum(r.get(k) or 0 for r in rows) for k in fields}
    result["calls"] = sum(r.get("calls", 1) for r in rows)
    amounts = [r for r in rows if r.get("cost") is not None]
    result["cost"] = str(sum((Decimal(str(r["cost"])) for r in amounts), Decimal(0)))
    result["costed_calls"] = sum(r.get("calls", 1) for r in amounts)
    result["costed_records"] = len(amounts)
    result["sources"] = {
        s: str(sum((Decimal(str(r["cost"])) for r in amounts if r["cost_source"] == s), Decimal(0)))
        for s in ("reported", "estimated", "legacy")
    }
    valid_cache = [
        r
        for r in rows
        if r.get("cache_read_tokens") is not None and r.get("context_input_tokens") is not None
    ]
    context = sum(r["context_input_tokens"] for r in valid_cache)
    result["cache_hit_ratio"] = (
        sum(r["cache_read_tokens"] for r in valid_cache) / context if context else None
    )
    result["token_calls"] = sum(
        r.get("calls", 1) for r in rows if r.get("total_tokens") is not None
    )
    result["request_attempts"] = sum(r.get("request_attempts", 0) for r in rows)
    return result


def summary_row(source):
    spend = source.get("summary") or {}
    raw = {
        **spend,
        "cost": spend.get("total_cost_usd"),
        "cost_status": "estimated" if spend.get("cost_is_estimated") else "legacy",
    }
    return {
        **normalize_usage(raw),
        "id": "summary:" + source["id"],
        "calls": int(number(spend.get("n_llm_calls")) or 0),
        "granularity": "summary",
        "timestamp": None,
        "agent_name": "Historical summary",
        "task_id": None,
        "model": "Unknown",
        "provider": "Unknown",
        "step_number": None,
        "request_attempts": 0,
        "latency_ms": None,
        "step_duration_ms": None,
        "status": "summary_only",
    }


class UsageView:
    """Scope provider returns trusted source descriptors; HTTP never accepts paths."""

    def __init__(self, sources):
        self.sources = sources
        self.reader = UsageTraceReader()
        self.lock = threading.RLock()

    def snapshot(self):
        sources = {s["id"]: s for s in self.sources()}
        self.reader.update(
            [Path(s["log_root"]) / "trace" for s in sources.values() if s.get("log_root")]
        )
        seen, rows = set(), []
        for source in sources.values():
            traced = (
                self.reader.rows(Path(source["log_root"]) / "trace")
                if source.get("log_root")
                else []
            )
            expected = number((source.get("summary") or {}).get("n_llm_calls")) or 0
            # A partial archive is not an additional bill. Keep its authoritative
            # summary separately; do not fabricate a residual by subtracting fields.
            selected = (
                traced
                if traced and len(traced) >= expected
                else [summary_row(source)]
                if source.get("summary")
                else traced
            )
            for row in selected:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                rows.append(
                    {
                        **row,
                        "benchmark_task_id": source.get("task_id"),
                        "attempt_id": source["id"],
                        "time": stamp(row.get("timestamp")),
                    }
                )
        rows.sort(key=lambda r: (r.get("time") or 0, r["id"]))
        return rows

    def query(self, query=""):
        with self.lock:
            q = {k: v[-1] for k, v in parse_qs(query).items()}
            rows = self.snapshot()
            revision = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]
            view = q.get("view", "overview")
            if view == "call":
                row = next((r for r in rows if r["id"] == q.get("id")), None)
                if row is None:
                    raise ValueError("Call not found")
                return row
            facets = {
                k: sorted({str(r.get(k) or "Unknown") for r in rows})
                for k in ("agent_name", "model", "provider", "benchmark_task_id")
            }
            for key in facets:
                if q.get(key):
                    rows = [r for r in rows if str(r.get(key) or "Unknown") == q[key]]
            if q.get("cost_source"):
                rows = [r for r in rows if r["cost_source"] == q["cost_source"]]
            end = max((r["time"] for r in rows if r["time"] is not None), default=0)
            start = stamp(q.get("from"))
            until = stamp(q.get("to"))
            if q.get("range") == "last15":
                start = end - 900
            if start is not None:
                rows = [r for r in rows if r["time"] is not None and r["time"] >= start]
            if until is not None:
                rows = [r for r in rows if r["time"] is not None and r["time"] < until]
            if view == "export":
                out = io.StringIO()
                keys = [
                    "timestamp",
                    "id",
                    "granularity",
                    "agent_name",
                    "model",
                    "provider",
                    "benchmark_task_id",
                    "attempt_id",
                    "input_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "context_input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "total_tokens",
                    "cost",
                    "cost_source",
                    "request_attempts",
                    "step_duration_ms",
                ]
                writer = csv.DictWriter(out, fieldnames=keys)
                writer.writeheader()
                for r in rows:
                    writer.writerow(
                        {
                            k: (
                                "'" + v
                                if isinstance(v, str)
                                and v.startswith(("=", "+", "-", "@", "\t", "\r"))
                                else v
                            )
                            for k in keys
                            for v in [r.get(k)]
                        }
                    )
                return out.getvalue()
            if view not in {"overview", "calls"}:
                raise ValueError("Unknown usage view")
            detailed = [r for r in rows if r["time"] is not None and r["granularity"] != "summary"]
            historical = [r for r in rows if r["granularity"] == "summary"]
            metric = q.get("metric", "cost")
            if metric not in {"cost", "tokens", "calls", "latency"}:
                raise ValueError("Unknown metric")
            axis = q.get("axis", "call")
            groups = []
            if axis == "time":
                width = int(q.get("bucket", "60"))
                if width not in {5, 60, 300, 3600, 86400}:
                    raise ValueError("Invalid bucket")
                if detailed:
                    span = detailed[-1]["time"] - detailed[0]["time"]
                    width = max(width, int(span / 750) + 1)
                bins = {}
                for r in detailed:
                    bucket = int(r["time"] // width) * width
                    bins.setdefault(bucket, []).append(r)
                groups = [
                    (datetime.fromtimestamp(t, timezone.utc).isoformat(), rs)
                    for t, rs in sorted(bins.items())
                ]
            else:
                # Bound chart size without silently dropping consumption: adjacent
                # records are grouped and the response labels their exact coverage.
                batch = max(1, (len(detailed) + 749) // 750)
                groups = [
                    (str(i + 1), detailed[i : i + batch]) for i in range(0, len(detailed), batch)
                ]
            series = []
            cumulative = 0
            running_tokens = dict.fromkeys(TOKEN_SERIES, 0)
            running_coverage = dict.fromkeys(TOKEN_SERIES, 0)
            running_observations = 0
            running_conflicts = 0
            for label, part in groups:
                sums = total(part)
                vectors = [token_values(row) for row in part]
                parts, token_coverage = {}, {}
                token_observations = len(part)
                token_conflicts = sum(conflict for _, conflict in vectors)
                for field in TOKEN_SERIES:
                    values = [v[field] for v, _ in vectors if v[field] is not None]
                    parts[field] = sum(values) if values else None
                    token_coverage[field] = len(values)
                if q.get("cumulative") == "true":
                    running_observations += len(part)
                    running_conflicts += token_conflicts
                    token_observations = running_observations
                    token_conflicts = running_conflicts
                    for field in TOKEN_SERIES:
                        running_tokens[field] += parts[field] or 0
                        running_coverage[field] += token_coverage[field]
                        # Keep a gap when the current group has no observation;
                        # later values are explicitly cumulative known subtotals.
                        if parts[field] is not None:
                            parts[field] = running_tokens[field]
                        token_coverage[field] = running_coverage[field]
                known = [r for r in part if r.get("cost") is not None]
                duration = [
                    r["step_duration_ms"] for r in part if r.get("step_duration_ms") is not None
                ]
                value = (
                    (float(sums["cost"]) if known else None)
                    if metric == "cost"
                    else parts["total_tokens"]
                    if metric == "tokens"
                    else (sum(duration) / len(duration) / 1000 if duration else None)
                    if metric == "latency"
                    else sums["calls"]
                )
                if q.get("cumulative") == "true" and metric not in {"latency", "tokens"}:
                    cumulative += value or 0
                    value = cumulative if value is not None else None
                series.append(
                    {
                        "label": label,
                        "value": value,
                        "tokens": parts,
                        "token_coverage": token_coverage,
                        "token_observations": token_observations,
                        "token_conflicts": token_conflicts,
                        "count": len(part),
                        "costed": len(known),
                        "id": part[0]["id"] if len(part) == 1 else None,
                        "from": part[0]["timestamp"],
                        "to": part[-1]["timestamp"],
                    }
                )
            group = q.get("group_by", "agent_name")
            if group not in {"agent_name", "model", "provider", "benchmark_task_id"}:
                raise ValueError("Invalid group")
            buckets = {}
            for r in rows:
                buckets.setdefault(str(r.get(group) or "Unknown"), []).append(r)
            breakdown = sorted(
                ({"name": k, **total(v)} for k, v in buckets.items()),
                key=lambda r: float(r["cost"]),
                reverse=True,
            )
            page = max(0, int(q.get("page", "0")))
            limit = min(100, max(1, int(q.get("limit", "25"))))
            ordered = sorted(
                rows,
                key=(lambda r: (r.get("cost") is not None, r.get("cost") or 0))
                if q.get("sort") == "cost"
                else lambda r: (r["time"] or 0, r["id"]),
                reverse=True,
            )
            return {
                "schema_version": 1,
                "revision": revision,
                "summary": total(rows),
                "series": series,
                "breakdown": breakdown,
                "facets": facets,
                "calls": ordered[page * limit : (page + 1) * limit],
                "row_count": len(rows),
                "page": page,
                "limit": limit,
                "historical": total(historical),
                "coverage": {
                    "legacy_steps": len(detailed),
                    "summary_rows": len(historical),
                    "read_errors": self.reader.errors,
                },
                "granularity": "legacy_step",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

    def response(self, query):
        result = self.query(query)
        if isinstance(result, str):
            return result.encode("utf-8-sig"), "text/csv; charset=utf-8"
        return json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8"
