"""Website release gates are ordered, atomic, and isolated to the run manifest."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentevolver.tool.default.website_release_gate import WebsiteReleaseGateTool


def _ctx(workspace):
    return SimpleNamespace(extra={"execution_cwd": str(workspace)})


def _evidence(state: str, suffix: str = "0"):
    return {
        "BUILDING": {"plan_ref": f"plans/v{suffix}.html"},
        "VERIFYING": {"implementation_ref": f"revisions/v{suffix}"},
        "FROZEN": {
            "source_hash": f"sha256:{suffix}",
            "verification_ref": f"evidence/v{suffix}.json",
        },
        "PUBLISHED": {
            "deployment_id": "echo-ark-live",
            "url": "http://localhost:8000/",
            "publish_event_id": f"event-{suffix}",
            "fanout": 3,
        },
        "COLLECTING": {"participant_job_ids": ["user-a", "user-b", "user-c"]},
        "SYNTHESIZING": {
            "participant_outputs": {"user-a": "a", "user-b": "b", "user-c": "c"},
            "authoritative_records_ref": f"evidence/records-v{suffix}.json",
        },
        "PLANNING_NEXT": {
            "decision_ledger_ref": "decision_ledger.json",
            "contribution_ledger_ref": "contribution_ledger.json",
        },
    }[state]


@pytest.mark.asyncio
async def test_release_gate_preserves_manifest_and_records_the_exact_sequence(tmp_path):
    demo = tmp_path / "website_evolution_demo"
    demo.mkdir()
    manifest = demo / "run_manifest.json"
    manifest.write_text(json.dumps({"run_id": "run-1", "custom": {"kept": True}}))
    tool = WebsiteReleaseGateTool()
    expected = ""

    for state in (
        "BUILDING",
        "VERIFYING",
        "FROZEN",
        "PUBLISHED",
        "COLLECTING",
        "SYNTHESIZING",
        "PLANNING_NEXT",
    ):
        response = await tool(
            release_id="V0",
            expected_state=expected,
            next_state=state,
            evidence=_evidence(state),
            ctx=_ctx(tmp_path),
        )
        assert response.success, response.message
        expected = state

    document = json.loads(manifest.read_text())
    assert document["run_id"] == "run-1"
    assert document["custom"] == {"kept": True}
    record = document["release_gates"]["releases"]["V0"]
    assert record["state"] == "PLANNING_NEXT"
    assert [item["to"] for item in record["history"]] == list(
        document["release_gates"]["state_order"]
    )


@pytest.mark.asyncio
async def test_release_gate_rejects_stale_or_skipped_transition_without_mutation(tmp_path):
    tool = WebsiteReleaseGateTool()
    started = await tool(
        release_id="V0",
        expected_state="",
        next_state="BUILDING",
        evidence=_evidence("BUILDING"),
        ctx=_ctx(tmp_path),
    )
    assert started.success
    manifest = tmp_path / "website_evolution_demo" / "run_manifest.json"
    before = manifest.read_text()

    stale = await tool(
        release_id="V0",
        expected_state="",
        next_state="BUILDING",
        evidence=_evidence("BUILDING"),
        ctx=_ctx(tmp_path),
    )
    skipped = await tool(
        release_id="V0",
        expected_state="BUILDING",
        next_state="FROZEN",
        evidence=_evidence("FROZEN"),
        ctx=_ctx(tmp_path),
    )

    assert not stale.success and "stale transition" in stale.message
    assert not skipped.success and "illegal transition" in skipped.message
    assert manifest.read_text() == before


@pytest.mark.asyncio
async def test_next_release_waits_for_prior_synthesis_and_needs_a_new_hash(tmp_path):
    tool = WebsiteReleaseGateTool()
    ctx = _ctx(tmp_path)
    expected = ""
    for state in (
        "BUILDING",
        "VERIFYING",
        "FROZEN",
        "PUBLISHED",
        "COLLECTING",
        "SYNTHESIZING",
        "PLANNING_NEXT",
    ):
        assert (
            await tool(
                release_id="V0",
                expected_state=expected,
                next_state=state,
                evidence=_evidence(state),
                ctx=ctx,
            )
        ).success
        expected = state

    assert (
        await tool(
            release_id="V1",
            expected_state="",
            next_state="BUILDING",
            evidence=_evidence("BUILDING", "1"),
            ctx=ctx,
        )
    ).success
    assert (
        await tool(
            release_id="V1",
            expected_state="BUILDING",
            next_state="VERIFYING",
            evidence=_evidence("VERIFYING", "1"),
            ctx=ctx,
        )
    ).success
    duplicate = await tool(
        release_id="V1",
        expected_state="VERIFYING",
        next_state="FROZEN",
        evidence=_evidence("FROZEN"),
        ctx=ctx,
    )
    assert not duplicate.success and "duplicates frozen release V0" in duplicate.message


@pytest.mark.asyncio
async def test_release_gate_requires_three_subscribers_at_publish_and_collection(tmp_path):
    tool = WebsiteReleaseGateTool()
    bad_publish = _evidence("PUBLISHED") | {"fanout": 2}
    bad_collection = {"participant_job_ids": ["same", "same", "other"]}

    published = await tool(
        release_id="V0",
        expected_state="FROZEN",
        next_state="PUBLISHED",
        evidence=bad_publish,
        ctx=_ctx(tmp_path),
    )
    collecting = await tool(
        release_id="V0",
        expected_state="PUBLISHED",
        next_state="COLLECTING",
        evidence=bad_collection,
        ctx=_ctx(tmp_path),
    )

    assert not published.success and "fanout exactly 3" in published.message
    assert not collecting.success and "three distinct" in collecting.message
