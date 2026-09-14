"""Durable per-trial receipts for generated local research workers (POSIX).

No financial decisions or worker scheduling live here. Wrap each native numerical
action in execute(); collection/reporting is a projection of persisted receipts.
"""
from __future__ import annotations

from contextlib import contextmanager
import asyncio
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def atomic_json(path, value):
    raw = encoded(value)
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class TrialRecords:
    """Worker completion survives a missing agent-side collect or round summary."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self):
        with (self.root / ".lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def folder(self, trial_id):
        if not isinstance(trial_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", trial_id):
            raise ValueError("Trial ID must be a stable basename")
        path = self.root / trial_id
        path.mkdir(exist_ok=True)
        return path

    def event(self, path, state, **details):
        value = {"trial_id": path.name, "state": state,
                 "at": datetime.now(timezone.utc).isoformat(), **details}
        file = path / "events.jsonl"
        if file.exists():
            raw = file.read_bytes()
            if raw and not raw.endswith(b"\n"):
                boundary = raw.rfind(b"\n") + 1
                tail = raw[boundary:]
                # Preserve a killed writer's incomplete bytes without appending a
                # new JSON record to that fragment and corrupting the next event.
                (path / ("partial-event-" + hashlib.sha256(tail).hexdigest())).write_bytes(tail)
                with file.open("r+b") as stream:
                    stream.truncate(boundary)
        with file.open("ab") as stream:
            stream.write(encoded(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        return value

    def events(self, path):
        file = path / "events.jsonl"
        if not file.exists():
            return []
        # A killed writer may leave a partial last append; complete lines remain evidence.
        return [json.loads(line) for line in file.read_bytes().splitlines(keepends=True)
                if line.endswith(b"\n")]

    def _plan(self, trial_id, request):
        path = self.folder(trial_id)
        file = path / "request.json"
        if file.exists():
            if sha(json.loads(file.read_text())) != sha(request):
                raise ValueError("A changed request needs a new trial ID")
        else:
            atomic_json(file, request)
            self.event(path, "planned", request_sha256=sha(request))
        return path

    def plan(self, trial_id, request):
        with self.locked():
            return str(self._plan(trial_id, request) / "request.json")

    def verify(self, path):
        envelope = json.loads((path / "result.json").read_text())
        if envelope["request_sha256"] != sha(json.loads((path / "request.json").read_text())):
            raise ValueError("Result/request identity mismatch")
        if envelope["trial_id"] != path.name or envelope["status"] != "computed":
            raise ValueError("Invalid completion receipt")
        result_hash = sha(envelope)
        recorded = [e["result_sha256"] for e in self.events(path) if "result_sha256" in e]
        if any(value != result_hash for value in recorded):
            raise ValueError("Completed result changed after it was recorded")
        for output in envelope["outputs"]:
            file = Path(output["path"])
            if not file.is_absolute():
                file = path / file
            if hashlib.sha256(file.read_bytes()).hexdigest() != output["sha256"]:
                raise ValueError(f"Result output hash mismatch: {file}")
        return envelope, result_hash

    def execute(self, trial_id, request, compute):
        """compute() returns {payload: JSON object, outputs: [{path, sha256}]}.

        Exceptions are execution errors; a valid weak/negative market result is a
        computed result. A cached return does not by itself mean the agent read it.
        """
        with self.locked():
            path = self._plan(trial_id, request)
            if (path / "result.json").exists():
                _, digest = self.verify(path)
                return {"trial_id": trial_id, "result_path": str(path / "result.json"),
                        "sha256": digest, "cached": True}
            events = self.events(path)
            if events and events[-1]["state"] == "dispatched":
                raise ValueError("Trial is already dispatched; reconcile after worker shutdown before retry")
            attempt = 1 + sum(e["state"] == "dispatched" for e in events)
            self.event(path, "dispatched", attempt=attempt)
        try:
            result = compute()
            if not isinstance(result, dict) or not isinstance(result.get("payload"), dict):
                raise ValueError("Worker must return a payload object")
            outputs = result.get("outputs")
            if not isinstance(outputs, list):
                raise ValueError("Worker must declare output paths/hashes (possibly empty)")
            for output in outputs:
                if (not isinstance(output, dict) or not isinstance(output.get("path"), str)
                        or not re.fullmatch(r"[0-9a-f]{64}", output.get("sha256", ""))):
                    raise ValueError("Invalid output path/hash")
                file = Path(output["path"])
                if not file.is_absolute():
                    file = path / file
                if hashlib.sha256(file.read_bytes()).hexdigest() != output["sha256"]:
                    raise ValueError("Worker output hash mismatch")
            envelope = {"schema": 1, "trial_id": trial_id, "status": "computed", "attempt": attempt,
                        "request_sha256": sha(request), "outputs": outputs, "payload": result["payload"]}
            with self.locked():
                # Publish completion before any agent-side catalog/report operation.
                atomic_json(path / "result.json", envelope)
                self.event(path, "computed", attempt=attempt, result_sha256=sha(envelope))
            return {"trial_id": trial_id, "result_path": str(path / "result.json"),
                    "sha256": sha(envelope), "cached": False}
        except BaseException as error:
            with self.locked():
                self.event(path, "interrupted" if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError))
                           else "error", attempt=attempt, error_type=type(error).__name__)
            raise

    def observe(self, trial_id):
        """Mark actual consumption; call when exposing results to the researcher."""
        with self.locked():
            path = self.folder(trial_id)
            envelope, digest = self.verify(path)
            self.event(path, "observed", result_sha256=digest)
            return envelope["payload"]

    def reconcile(self, *, workers_stopped=False):
        """Recover computed receipts; only mark dispatched work interrupted after join.

        Reads status/hash metadata, not market values into the agent's context.
        Planned requests remain planned; they are never counted as data exposure.
        """
        rows = []
        with self.locked():
            for path in sorted(self.root.iterdir()):
                if not path.is_dir() or not (path / "request.json").exists():
                    continue
                events = self.events(path)
                observed = any(e["state"] == "observed" for e in events)
                state = events[-1]["state"] if events else "planned"
                if (path / "result.json").exists():
                    try:
                        _, digest = self.verify(path)
                    except (KeyError, TypeError, ValueError, OSError):
                        state = "invalid"
                    else:
                        state = "computed"
                        if not any(e["state"] == "computed" for e in events):
                            self.event(path, "computed", recovered=True, result_sha256=digest)
                elif state == "dispatched" and workers_stopped:
                    self.event(path, "interrupted", reason="worker stopped without completion receipt")
                    state = "interrupted"
                rows.append({"trial_id": path.name, "state": state, "observed": observed,
                             "result_path": str(path / "result.json") if state == "computed" else None})
        return rows

    def reserve_final(self, bundle, *, prior_exposure):
        """Record one immutable final bundle BEFORE retrieval, across both engines.

        Caller first verifies its readiness evidence. This ledger prevents accidental
        retries with another bundle; editable local files do not enforce isolation
        from their author. prior_exposure is separate from this run's access state.
        """
        if not isinstance(bundle, dict) or not bundle:
            raise ValueError("Final access requires a nonempty frozen bundle")
        if prior_exposure not in ("previously_exposed", "no_known_exposure", "unknown"):
            raise ValueError("Declare prior historical exposure separately")
        with self.locked():
            path = self.root / "final-access.json"
            if path.exists():
                receipt = json.loads(path.read_text())
                if sha(receipt["bundle"]) != receipt["bundle_sha256"]:
                    raise ValueError("Stored final bundle changed after reservation")
                if receipt["bundle_sha256"] != sha(bundle) or receipt["prior_exposure"] != prior_exposure:
                    raise ValueError("Final access already reserved for a different bundle/history")
                return {**receipt, "replay": True}
            receipt = {"schema": 1, "bundle": bundle, "bundle_sha256": sha(bundle),
                       "prior_exposure": prior_exposure,
                       "at": datetime.now(timezone.utc).isoformat()}
            atomic_json(path, receipt)
            return {**receipt, "replay": False}

    def bind_final_snapshot(self, bundle, snapshot):
        """Bind the real downloaded file once, after access reservation and before scoring."""
        snapshot = Path(snapshot).resolve()
        with self.locked():
            access = json.loads((self.root / "final-access.json").read_text())
            if sha(access["bundle"]) != access["bundle_sha256"]:
                raise ValueError("Stored final bundle changed after reservation")
            if access["bundle_sha256"] != sha(bundle):
                raise ValueError("Snapshot does not belong to the reserved final bundle")
            value = {"path": str(snapshot), "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                     "bundle_sha256": sha(bundle)}
            file = self.root / "final-snapshot.json"
            if file.exists():
                if json.loads(file.read_text()) != value:
                    raise ValueError("Final snapshot changed after binding")
            else:
                atomic_json(file, value)
            return value
