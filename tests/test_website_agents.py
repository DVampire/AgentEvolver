"""Website-specific actors keep role routing and capability isolation deterministic."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentevolver.agent.actor.website_builder_agent import (
    bind_runtime_input_manifest,
)
from agentevolver.agent.actor.website_user_agent import WebsiteUserAgent


def _task(manifest=None):
    payload = manifest or {
        "site_brief": "/source/site.html",
        "persona_01": "/source/p1.html",
        "persona_02": "/source/p2.html",
        "persona_03": "/source/p3.html",
        "optimization_cycles": 5,
    }
    return (
        "Build the scenario.\n\n"
        "## runtime-input-manifest\n"
        "Role-only attachment routing.\n"
        f"{json.dumps(payload)}"
    )


def test_builder_rebinds_role_manifest_to_staged_files_without_reading_them():
    staged = [f"/session/log/inputs/00{index}_input.html" for index in range(4)]
    bound = bind_runtime_input_manifest(_task(), staged)
    manifest = json.loads(bound[bound.index("{"):])

    assert manifest["site_brief"] == staged[0]
    assert [manifest[f"persona_0{index}"] for index in range(1, 4)] == staged[1:]
    assert manifest["optimization_cycles"] == 5
    assert manifest["paths_staged"] is True
    assert "/source/p1.html" not in bound


def test_builder_mounts_job_without_opening_its_own_browser_session():
    from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent

    ctx = SimpleNamespace(extra={})
    WebsiteBuilderAgent._bind_runtime_environment(ctx)
    assert ctx.extra["environment_allowlist"] == ["job"]


@pytest.mark.parametrize(
    "task,files,error",
    [
        ("no manifest", ["a", "b", "c", "d"], "missing runtime-input-manifest"),
        (_task(), ["only-site"], "requires four staged attachments"),
        (
            "x\n## runtime-input-manifest\nnot-json",
            ["a", "b", "c", "d"],
            "has no JSON object",
        ),
    ],
)
def test_builder_rejects_ambiguous_role_routing(task, files, error):
    with pytest.raises(ValueError, match=error):
        bind_runtime_input_manifest(task, files)


def test_website_user_is_browser_only_even_when_dispatch_omits_allowlists():
    agent = WebsiteUserAgent(base_dir=".")
    assert agent._required_capability_allowlists() == {
        "tool_allowlist": ["done_tool", "escalate_tool"],
        "skill_allowlist": [],
        "connector_allowlist": [],
        "plugin_allowlist": [],
        "environment_allowlist": ["browser_environment"],
        "workflow_allowlist": [],
    }


def test_website_user_resets_only_per_release_budgets_between_events():
    agent = WebsiteUserAgent(
        base_dir=".", max_step=30, max_token=1000, timeout=60,
    )
    ctx = SimpleNamespace(id="resident-session")
    for constraint in agent.constraints:
        constraint._state[ctx.id] = {"sentinel": True}
    agent._pending_step_tokens["release-task"] = 123

    agent._reset_release_budget(ctx)

    assert all(ctx.id not in constraint._state for constraint in agent.constraints)
    assert agent._pending_step_tokens == {}
