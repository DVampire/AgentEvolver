"""Portable, read-only usage projection for archived JSONL traces.

No provider clients or price tables are loaded here. Legacy agent_call records remain
explicitly step-granular. A model_request snapshot supplies attribution, not a made-up
model latency or an extra bill. Only bounded, public metadata survives projection.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

TOKEN_FIELDS = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens")


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def normalize_usage(raw):
    raw = raw if isinstance(raw, dict) else {}
    tokens = {key: number(raw.get(key)) for key in TOKEN_FIELDS}
    context = number(raw.get("context_input_tokens"))
    if context is None or context == 0:
        present = [tokens[k] for k in TOKEN_FIELDS[:3] if tokens[k] is not None]
        context = sum(tokens[k] or 0 for k in TOKEN_FIELDS[:3]) if present else None
    output = tokens["output_tokens"]
    cost = number(raw.get("cost"))
    return {
        **tokens,
        "context_input_tokens": context,
        "total_tokens": context + output if context is not None and output is not None else None,
        "reasoning_tokens": number(raw.get("reasoning_tokens")),
        "cost": cost,
        "cost_source": (
            raw.get("cost_status")
            if raw.get("cost_status") in {"reported", "estimated"}
            else "legacy"
        )
        if cost is not None
        else "unknown",
    }


def key(event):
    return (event.get("session_id"), event.get("task_id"), event.get("step_number"))


class UsageTraceReader:
    """Incremental projection; identity-aware and safe across partial append/rotation."""

    def __init__(self):
        self.files = {}
        self.by_root = {}
        self.errors = 0

    def update(self, roots):
        paths = set()
        self.errors = 0
        for root in roots:
            root = Path(root).resolve()
            try:
                paths.update(p for p in root.glob("*.jsonl") if p.resolve().is_relative_to(root))
            except OSError:
                self.errors += 1
        for gone in self.files.keys() - paths:
            del self.files[gone]
        for path in sorted(paths):
            try:
                st = path.stat()
                ident = (st.st_dev, st.st_ino)
                item = self.files.get(path)
                if not item or item["identity"] != ident or st.st_size < item["offset"]:
                    item = dict(identity=ident, offset=0, events={}, errors=0)
                    self.files[path] = item
                with path.open("rb") as stream:
                    stream.seek(item["offset"])
                    while line := stream.readline():
                        if not line.endswith(b"\n"):
                            break
                        offset = item["offset"]
                        item["offset"] += len(line)
                        try:
                            event = json.loads(line)
                            if not isinstance(event, dict):
                                continue
                            typ = event.get("event_type")
                            metadata = event.get("metadata") or {}
                            receipt = typ == "custom" and metadata.get("type") == "model_usage"
                            if typ not in {"agent_call", "model_request"} and not receipt:
                                continue
                            identity = str(
                                event.get("id")
                                or hashlib.sha256(f"{path}:{offset}".encode()).hexdigest()
                            )
                            event_id = str(event.get("session_id") or "") + ":" + identity
                            row = {
                                k: event.get(k)
                                for k in (
                                    "session_id",
                                    "task_id",
                                    "step_number",
                                    "agent_name",
                                    "timestamp",
                                    "seq_no",
                                )
                            }
                            row.update(id=event_id, event_type="model_usage" if receipt else typ)
                            if typ == "model_request":
                                data = event.get("input") or {}
                                row.update(
                                    model=str(
                                        data.get("routed_model")
                                        or data.get("requested_model")
                                        or "Unknown"
                                    ),
                                    provider=str(data.get("provider") or "Unknown"),
                                    operation=str((data.get("parameters") or {}).get("operation") or "generation"),
                                )
                            elif receipt:
                                row.update(normalize_usage(event.get("usage")))
                                row.update(
                                    model=str(metadata.get("model") or "Unknown"),
                                    provider=str(metadata.get("provider") or "Unknown"),
                                    operation=str(metadata.get("operation") or "generation"),
                                    success=event.get("success") is not False,
                                    snapshot_id=metadata.get("request_snapshot_id"),
                                )
                            else:
                                row.update(normalize_usage(event.get("usage")))
                                row["step_duration_ms"] = number(event.get("duration_ms"))
                            item["events"][event_id] = row
                        except (ValueError, TypeError, AttributeError, UnicodeError):
                            item["errors"] += 1
            except OSError:
                self.errors += 1
        self.errors += sum(item["errors"] for item in self.files.values())
        self.by_root = {}
        for path, item in self.files.items():
            self.by_root.setdefault(path.parent.resolve(), []).append(item)
        return self

    def rows(self, root):
        root = Path(root).resolve()
        events = {}
        for item in self.by_root.get(root, []):
            events.update(item["events"])
        requests, receipts, rows = {}, {}, []
        ordered = sorted(
            events.values(),
            key=lambda e: (
                e.get("timestamp") or "",
                e["seq_no"] if type(e.get("seq_no")) is int else
                {"model_request": 0, "model_usage": 1, "agent_call": 2}[e["event_type"]],
                e["id"],
            ),
        )
        for event in ordered:
            if event["event_type"] == "model_request":
                requests.setdefault(key(event), []).append(event)
                continue
            if event["event_type"] == "model_usage":
                receipts.setdefault(key(event), []).append(event)
                rows.append({
                    **event, "granularity": "request", "calls": 1,
                    "request_attempts": 1, "latency_ms": None, "step_duration_ms": None,
                    "status": "completed" if event["success"] else "failed",
                })
                continue
            # Step numbers can restart on later resident turns. Consume only starts
            # preceding this completion, never requests belonging to a later turn.
            matches = requests.pop(key(event), [])
            completed = receipts.pop(key(event), [])
            if any(r["operation"] == "generation" and r["success"] for r in completed):
                continue  # The step is a projection of these receipts, not another bill.
            models = {r["model"] for r in matches}
            providers = {r["provider"] for r in matches}
            rows.append(
                {
                    **event,
                    "model": next(iter(models))
                    if len(models) == 1
                    else "Multiple routes"
                    if models
                    else "Unknown",
                    "provider": next(iter(providers)) if len(providers) == 1 else "Unknown",
                    "request_attempts": len(matches),
                    "granularity": "legacy_step",
                    "calls": 1,
                    "latency_ms": None,
                    "status": "step_completed",
                }
            )
        return rows
