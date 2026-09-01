"""Atomic release-state transitions for the website evolution demonstration."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from pydantic import Field

from agentevolver.paths import path_manager
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.session import resolve_workspace_root
from agentevolver.tool.types import Tool
from agentevolver.utils.file_utils import atomic_json_update


_STATES = (
    "BUILDING",
    "VERIFYING",
    "FROZEN",
    "PUBLISHED",
    "COLLECTING",
    "SYNTHESIZING",
    "PLANNING_NEXT",
)
_VERSION = re.compile(r"^V(0|[1-9][0-9]*)$")
_DESCRIPTION = (
    "Atomically advance one website release through the audited build, freeze, publish, "
    "collection, and synthesis gates. This enforces release integrity without prescribing "
    "what the website iteration must contain."
)
_GUIDANCE = """
Call this once at each release boundary. The only legal sequence is:
`BUILDING → VERIFYING → FROZEN → PUBLISHED → COLLECTING → SYNTHESIZING → PLANNING_NEXT`.

- Start V0 with `expected_state=""`, `next_state="BUILDING"`. Start a later release only
  after the preceding release reaches `PLANNING_NEXT`.
- Always pass the state you observed as `expected_state`; a stale concurrent update is rejected.
- Evidence is transition-specific. The error response lists missing or invalid fields.
- The tool writes only the `release_gates` section of the demo's `run_manifest.json` and
  preserves the builder's remaining manifest schema.
"""
_EXAMPLES = [
    '{"name":"website_release_gate_tool","args":{"release_id":"V0",'
    '"expected_state":"","next_state":"BUILDING","evidence":'
    '{"plan_ref":"website_v0_spec.html"}}}',
    '{"name":"website_release_gate_tool","args":{"release_id":"V0",'
    '"expected_state":"VERIFYING","next_state":"FROZEN","evidence":'
    '{"source_hash":"sha256:...","verification_ref":"evidence/v0.json"}}}',
]


class _GateError(ValueError):
    """A user-correctable release transition failure."""


def _present(evidence: Dict[str, Any], key: str) -> bool:
    value = evidence.get(key)
    return value is not None and value != "" and value != [] and value != {}


def _three_distinct(value: Any) -> bool:
    if isinstance(value, dict):
        items = list(value.keys())
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return False
    return len(items) == 3 and len({str(item) for item in items}) == 3


def _validate_evidence(next_state: str, evidence: Dict[str, Any]) -> None:
    required = {
        "BUILDING": ("plan_ref",),
        "VERIFYING": ("implementation_ref",),
        "FROZEN": ("source_hash", "verification_ref"),
        "PUBLISHED": ("deployment_id", "url", "publish_event_id", "fanout"),
        "COLLECTING": ("participant_job_ids",),
        "SYNTHESIZING": ("participant_outputs", "authoritative_records_ref"),
        "PLANNING_NEXT": ("decision_ledger_ref", "contribution_ledger_ref"),
    }[next_state]
    missing = [key for key in required if not _present(evidence, key)]
    if missing:
        raise _GateError(f"{next_state} requires evidence fields: {', '.join(missing)}")
    if next_state == "PUBLISHED" and evidence.get("fanout") != 3:
        raise _GateError("PUBLISHED requires publish fanout exactly 3")
    if next_state == "COLLECTING" and not _three_distinct(
        evidence.get("participant_job_ids")
    ):
        raise _GateError("COLLECTING requires exactly three distinct participant job IDs")
    if next_state == "SYNTHESIZING" and not _three_distinct(
        evidence.get("participant_outputs")
    ):
        raise _GateError("SYNTHESIZING requires outputs from exactly three participants")
    try:
        json.dumps(evidence, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise _GateError(f"evidence must be JSON serializable: {error}") from error


@TOOL.register_module(force=True)
class WebsiteReleaseGateTool(Tool):
    """Compare-and-swap release gates stored in the website run manifest."""

    name: str = "website_release_gate_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = False
    permission_mode: str = "workspace_write"
    mutates: bool = True
    max_release: int = Field(default=5, ge=0)

    async def __call__(
        self,
        release_id: str,
        expected_state: str,
        next_state: str,
        evidence: Dict[str, Any],
        **kwargs: Any,
    ) -> Response:
        """Advance one release by exactly one state using compare-and-swap semantics.

        Args:
            release_id: Version identifier such as V0 or V3.
            expected_state: State currently observed by the caller, or empty for a new release.
            next_state: Immediate next state in the configured release sequence.
            evidence: JSON-safe transition evidence required for the requested next state.
            **kwargs: Runtime-injected values, including the active Agent context.

        Returns:
            A response naming the persisted state and manifest, or a non-mutating rejection.
        """
        ctx = kwargs.get("ctx")
        workspace = resolve_workspace_root(ctx)
        if not workspace:
            return self._failure("an active workspace is required")

        release = str(release_id or "").strip().upper()
        expected = str(expected_state or "").strip().upper()
        target = str(next_state or "").strip().upper()
        match = _VERSION.fullmatch(release)
        if match is None:
            return self._failure("release_id must be V0, V1, ...")
        release_number = int(match.group(1))
        if release_number > self.max_release:
            return self._failure(f"{release} exceeds configured final release V{self.max_release}")
        if target not in _STATES:
            return self._failure(f"next_state must be one of: {', '.join(_STATES)}")
        if expected and expected not in _STATES:
            return self._failure(f"expected_state must be empty or one of: {', '.join(_STATES)}")
        try:
            _validate_evidence(target, dict(evidence or {}))
        except _GateError as error:
            return self._failure(str(error))

        demo_root = path_manager.resolve_under(
            workspace, "website_evolution_demo", create=True
        )
        manifest_path = path_manager.resolve_under(demo_root, "run_manifest.json")
        now = datetime.now(timezone.utc).isoformat()
        try:
            updated = atomic_json_update(
                manifest_path,
                lambda current: self._transition(
                    current=current,
                    release=release,
                    release_number=release_number,
                    expected=expected,
                    target=target,
                    evidence=dict(evidence or {}),
                    now=now,
                ),
                default={},
                recover_corrupt=False,
            )
        except (OSError, ValueError, TypeError) as error:
            return self._failure(str(error))

        gate = updated["release_gates"]["releases"][release]
        return Response(
            type=ResponseType.TOOL,
            success=True,
            message=f"Advanced {release} from {expected or '<new>'} to {target}.",
            data={
                "release_id": release,
                "state": gate["state"],
                "manifest": str(manifest_path),
                "transition_count": len(gate["history"]),
            },
        )

    def _transition(
        self,
        *,
        current: Any,
        release: str,
        release_number: int,
        expected: str,
        target: str,
        evidence: Dict[str, Any],
        now: str,
    ) -> Dict[str, Any]:
        if current is None:
            current = {}
        if not isinstance(current, dict):
            raise _GateError("run_manifest.json must contain a JSON object")
        document = dict(current)
        gates = dict(document.get("release_gates") or {})
        releases = dict(gates.get("releases") or {})
        record = releases.get(release)

        if record is None:
            if expected or target != "BUILDING":
                raise _GateError(
                    f"new {release} must transition from an empty expected_state to BUILDING"
                )
            if release_number == 0:
                if releases:
                    raise _GateError("V0 must be the first release")
            else:
                previous = releases.get(f"V{release_number - 1}")
                if not isinstance(previous, dict) or previous.get("state") != "PLANNING_NEXT":
                    raise _GateError(
                        f"V{release_number - 1} must reach PLANNING_NEXT before {release} starts"
                    )
            record = {"state": "", "history": []}
        elif not isinstance(record, dict):
            raise _GateError(f"release_gates.releases.{release} must be an object")

        actual = str(record.get("state") or "")
        if actual != expected:
            raise _GateError(
                f"stale transition for {release}: expected {expected or '<new>'}, "
                f"manifest is {actual or '<new>'}"
            )
        if actual == _STATES[-1]:
            raise _GateError(f"{release} is already complete at PLANNING_NEXT")
        expected_target = "BUILDING" if not actual else _STATES[_STATES.index(actual) + 1]
        if target != expected_target:
            raise _GateError(f"illegal transition for {release}: {actual or '<new>'} → {target}")

        if target == "FROZEN":
            source_hash = str(evidence["source_hash"])
            for other_id, other in releases.items():
                if other_id == release or not isinstance(other, dict):
                    continue
                for transition in other.get("history") or []:
                    if transition.get("to") == "FROZEN" and str(
                        (transition.get("evidence") or {}).get("source_hash")
                    ) == source_hash:
                        raise _GateError(
                            f"{release} source_hash duplicates frozen release {other_id}"
                        )

        history = list(record.get("history") or [])
        history.append(
            {
                "from": actual or None,
                "to": target,
                "at": now,
                "evidence": evidence,
            }
        )
        releases[release] = {**record, "state": target, "history": history}
        gates.update(
            {
                "state_order": list(_STATES),
                "current_release": release,
                "updated_at": now,
                "releases": releases,
            }
        )
        document["release_gates"] = gates
        return document

    @staticmethod
    def _failure(message: str) -> Response:
        return Response(
            type=ResponseType.TOOL,
            success=False,
            message=f"Release gate was not advanced: {message}",
        )
