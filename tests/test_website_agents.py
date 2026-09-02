"""Website-specific actors keep role routing and capability isolation deterministic."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentevolver.agent.actor.website_builder_agent import (
    bind_runtime_input_manifest,
)
from agentevolver.agent.actor.website_user_agent import WebsiteUserAgent


def _task(manifest=None):
    payload = manifest or {
        "attachments": [
            {"id": "brief", "role": "requirements", "source_path": "/source/site.html"},
            {"id": "user-a", "role": "user_context", "source_path": "/source/p1.html"},
            {"id": "user-b", "role": "user_context", "source_path": "/source/p2.html"},
            {"id": "user-c", "role": "user_context", "source_path": "/source/p3.html"},
        ],
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

    assert [item["path"] for item in manifest["attachments"]] == staged
    assert [item["role"] for item in manifest["attachments"]] == [
        "requirements", "user_context", "user_context", "user_context",
    ]
    assert all(item["staged"] for item in manifest["attachments"])
    assert manifest["optimization_cycles"] == 5
    assert manifest["paths_staged"] is True
    assert "/source/p1.html" not in bound


def test_builder_mounts_job_without_opening_its_own_browser_session():
    from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent

    ctx = SimpleNamespace(extra={})
    WebsiteBuilderAgent._bind_runtime_environment(ctx)
    assert ctx.extra["environment_allowlist"] == ["job"]


def test_builder_accepts_an_ordinary_task_without_a_manifest():
    assert bind_runtime_input_manifest("Build a portfolio.", ["brief.md"]) == "Build a portfolio."


def test_builder_manifest_supports_task_defined_attachment_counts():
    task = _task({
        "attachments": [
            {"id": "requirements", "role": "brief", "source_path": "/old/a"},
            {"id": "brand", "role": "reference", "source_path": "/old/b"},
        ]
    })
    bound = bind_runtime_input_manifest(task, ["/staged/a", "/staged/b"])
    manifest = json.loads(bound[bound.index("{"):])
    assert [item["path"] for item in manifest["attachments"]] == [
        "/staged/a", "/staged/b",
    ]


@pytest.mark.parametrize(
    "task,files,error",
    [
        (_task(), ["only-site"], "attachment count does not match"),
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
        "tool_allowlist": ["done_tool"],
        "skill_allowlist": [],
        "connector_allowlist": [],
        "plugin_allowlist": [],
        "environment_allowlist": ["browser_environment"],
        "workflow_allowlist": [],
    }


def test_website_user_resets_only_per_task_budgets_between_turns():
    agent = WebsiteUserAgent(
        base_dir=".", max_step=30, max_token=1000, timeout=60,
    )
    ctx = SimpleNamespace(id="resident-session")
    for constraint in agent.constraints:
        constraint._state[ctx.id] = {"sentinel": True}
    agent._pending_step_tokens["current-task"] = 123

    agent._reset_turn_budget(ctx)

    assert all(ctx.id not in constraint._state for constraint in agent.constraints)
    assert agent._pending_step_tokens == {}


def test_builder_requires_verification_at_the_exact_deployed_url():
    prompt = (
        Path(__file__).resolve().parents[1]
        / "agentevolver"
        / "prompt"
        / "default"
        / "website_builder_agent.html"
    ).read_text(encoding="utf-8")

    assert "exact URL returned by" in prompt
    assert "including any path prefix" in prompt
    assert "Never substitute a direct source port" in prompt


def test_website_demo_mounts_only_distinct_agents_tools_and_skills():
    from mmengine import Config

    cfg = Config.fromfile(str(Path(__file__).resolve().parents[1] / "configs" / "website_evolution_demo.py"))

    assert cfg.agent_names == [
        "website_builder_agent",
        "generate_agent",
        "optimize_agent",
        "evaluate_agent",
        "website_user_agent",
    ]
    assert cfg.tool_names == [
        "bash_tool",
        "apply_patch_tool",
        "deploy_tool",
        "done_tool",
        "send_message_tool",
        "evolution_tool",
    ]
    assert cfg.skill_names == [
        "frontend_ui_engineering_skill",
        "webapp_testing_skill",
        "self_evolving_skill",
        "generate_skill",
        "optimize_skill",
        "evaluate_skill",
    ]


def test_website_builder_owns_product_engineering_directly():
    prompt = (
        Path(__file__).resolve().parents[1]
        / "agentevolver"
        / "prompt"
        / "default"
        / "website_builder_agent.html"
    ).read_text(encoding="utf-8")

    assert "Own product engineering end to end" in prompt
    assert "Use `bash_tool` for local inspection, search, scaffolding, Git" in prompt
    assert "use `apply_patch_tool` for authored source/configuration changes" in prompt
    for redundant_worker in ("code_agent", "general_agent", "reviewer_agent"):
        assert redundant_worker not in prompt


def test_website_prompts_do_not_encode_one_demo_protocol():
    prompt_dir = Path(__file__).resolve().parents[1] / "agentevolver" / "prompt" / "default"
    text = "\n".join(
        (prompt_dir / name).read_text(encoding="utf-8")
        for name in ("website_builder_agent.html", "website_user_agent.html")
    )
    for fixed_demo_term in (
        "V0", "V1", "V5", "exactly five", "website_evolution_demo",
        "feedback_ledger.json", "preference_ledger.json",
    ):
        assert fixed_demo_term not in text
