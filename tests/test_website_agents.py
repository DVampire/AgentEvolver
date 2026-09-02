"""Website-specific actors keep role routing and capability isolation deterministic."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentevolver.agent.actor.browser_agent import BrowserAgent
from agentevolver.agent.actor.website_builder_agent import (
    WebsiteBuilderAgent,
    _bound_runtime_input_manifest,
    bind_runtime_input_manifest,
)
from agentevolver.agent.actor.website_user_agent import WebsiteUserAgent
from agentevolver.environment.default.browser.service import BrowserService
from agentevolver.environment.default.job.environment import JobEnvironment
from agentevolver.tool.default.deployment.deploy import DeployTool
from agentevolver.tool.default.evolution import EvolutionTool


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
    manifest = json.loads(bound[bound.index("{") :])

    assert [item["role"] for item in manifest["attachments"]] == [
        "requirements",
        "user_context",
        "user_context",
        "user_context",
    ]
    assert manifest["attachments"][0]["path"] == staged[0]
    assert manifest["attachments"][0]["staged"] is True
    assert all(
        "path" not in item and item["routing"] == "runtime_private"
        for item in manifest["attachments"][1:]
    )
    assert manifest["optimization_cycles"] == 5
    assert manifest["paths_staged"] is True
    assert "/source/p1.html" not in bound
    assert staged[1] not in bound


def test_builder_mounts_job_without_opening_its_own_browser_session():
    from agentevolver.agent.actor.website_builder_agent import WebsiteBuilderAgent

    ctx = SimpleNamespace(extra={})
    WebsiteBuilderAgent._bind_runtime_environment(ctx)
    assert ctx.extra["environment_allowlist"] == ["job"]


def test_builder_accepts_an_ordinary_task_without_a_manifest():
    assert bind_runtime_input_manifest("Build a portfolio.", ["brief.md"]) == "Build a portfolio."


def test_builder_manifest_supports_task_defined_attachment_counts():
    task = _task(
        {
            "attachments": [
                {"id": "requirements", "role": "brief", "source_path": "/old/a"},
                {"id": "brand", "role": "reference", "source_path": "/old/b"},
            ]
        }
    )
    bound = bind_runtime_input_manifest(task, ["/staged/a", "/staged/b"])
    manifest = json.loads(bound[bound.index("{") :])
    assert [item["path"] for item in manifest["attachments"]] == [
        "/staged/a",
        "/staged/b",
    ]


@pytest.mark.asyncio
async def test_runtime_privately_bootstraps_one_browser_subscriber_per_user(tmp_path, monkeypatch):
    requirement = tmp_path / "scenario.html"
    requirement.write_text("<main>Public product requirement.</main>", encoding="utf-8")
    personas = []
    for index in range(1, 4):
        path = tmp_path / f"persona_{index}.html"
        path.write_text(f"<main>Private user {index} goal.</main>", encoding="utf-8")
        personas.append(path)
    manifest = {
        "attachments": [
            {"id": "brief", "role": "requirements"},
            *[{"id": f"persona_{index}", "role": "user_context"} for index in range(1, 4)],
        ],
        "optimization_cycles": 5,
        "participants": [
            {
                "id": f"user_{index}",
                "user_context_attachment": f"persona_{index}",
                "model": f"provider/model-{index}",
            }
            for index in range(1, 4)
        ],
        "release_acceptance": {"agent": "browser_agent", "model": "provider/judge"},
    }
    task = _task(manifest)
    private = _bound_runtime_input_manifest(
        task,
        [str(requirement), *(str(path) for path in personas)],
    )
    assert private is not None
    before, explanation, bound = private

    calls = []

    async def subscriber(name, **kwargs):
        calls.append((name, kwargs))
        return f"job-{len(calls)}"

    monkeypatch.setattr(WebsiteBuilderAgent, "_subscriber", staticmethod(subscriber))
    builder = WebsiteBuilderAgent(base_dir=str(tmp_path))
    ctx = SimpleNamespace(extra={})
    contract = await builder._bootstrap_subscribers(bound, ctx, ref=None)

    assert [name for name, _kwargs in calls] == [
        "website_user_agent",
        "website_user_agent",
        "website_user_agent",
        "browser_agent",
    ]
    for index, (_name, kwargs) in enumerate(calls[:3], start=1):
        assert f"Private user {index} goal." in kwargs["task"]
        assert all(
            f"Private user {other} goal." not in kwargs["task"]
            for other in range(1, 4)
            if other != index
        )
        assert kwargs.get("files") is None
    assert "Public product requirement." in calls[3][1]["task"]
    assert "Private user" not in calls[3][1]["task"]
    assert contract["subscriber_job_ids"] == ["job-1", "job-2", "job-3", "job-4"]
    assert contract["collected_turns"] == {}

    public = builder._public_manifest(before, explanation, bound, contract)
    assert all(str(path) not in public for path in personas)
    assert "provider/model" not in public
    assert "Private user" not in public


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


def test_browser_acceptance_is_browser_only_and_has_explicit_completion():
    agent = BrowserAgent(base_dir=".")
    assert agent._required_capability_allowlists() == {
        "tool_allowlist": ["done_tool"],
        "skill_allowlist": [],
        "connector_allowlist": [],
        "plugin_allowlist": [],
        "environment_allowlist": ["browser_environment"],
        "workflow_allowlist": [],
    }


def test_browser_native_diagnostics_aggregate_identical_events_losslessly():
    service = BrowserService()
    service._sessions["release"] = {
        "diagnostics": {},
        "diagnostic_seq": 0,
    }
    message = "TypeError: cannot read properties of undefined"
    service._record_diagnostic("release", "pageerror", message, "https://site.test/")
    service._record_diagnostic("release", "pageerror", message, "https://site.test/")

    diagnostics = service.diagnostics("release")

    assert diagnostics["total"] == 2
    assert diagnostics["counts"] == {"pageerror": 2}
    assert diagnostics["events"] == [
        {
            "type": "pageerror",
            "message": message,
            "url": "https://site.test/",
            "count": 2,
            "first_seq": 1,
            "last_seq": 2,
        }
    ]


@pytest.mark.asyncio
async def test_browser_command_rejects_javascript_with_python_guidance(monkeypatch):
    service = BrowserService()

    async def page_for(_session_id):
        return SimpleNamespace(context=object())

    monkeypatch.setattr(service, "_page_for", page_for)
    response = await service.command("const button = page.get_by_role('button', {name: /Save/});")

    assert response.success is False
    assert response.data["error"] == "wrong_command_language"
    assert "Playwright Python" in response.message


@pytest.mark.asyncio
async def test_concurrent_browser_users_get_distinct_contexts_and_pages():
    class Page:
        def on(self, _event, _callback):
            return None

        async def goto(self, _url):
            return None

        async def close(self):
            return None

    class Context:
        def __init__(self):
            self.page = Page()

        async def new_page(self):
            return self.page

        async def close(self):
            return None

    class Browser:
        def __init__(self):
            self.contexts = []

        async def new_context(self, **_kwargs):
            context = Context()
            self.contexts.append(context)
            return context

    service = BrowserService()
    browser = Browser()
    service._browser = browser

    first, second, third = await asyncio.gather(
        service._page_for("user-1"),
        service._page_for("user-2"),
        service._page_for("user-3"),
    )

    assert len(browser.contexts) == 3
    assert len({id(first), id(second), id(third)}) == 3
    assert {id(service._sessions[user]["context"]) for user in ("user-1", "user-2", "user-3")} == {
        id(context) for context in browser.contexts
    }


@pytest.mark.asyncio
async def test_browser_refuses_a_backend_that_cannot_isolate_sessions():
    class Browser:
        async def new_context(self, **_kwargs):
            raise NotImplementedError("shared CDP context only")

    service = BrowserService()
    service._browser = Browser()

    with pytest.raises(RuntimeError, match="cannot create an isolated BrowserContext"):
        await service._page_for("private-user")


def test_website_user_resets_only_per_task_budgets_between_turns():
    agent = WebsiteUserAgent(
        base_dir=".",
        max_step=30,
        max_token=1000,
        timeout=60,
    )
    ctx = SimpleNamespace(id="resident-session")
    for constraint in agent.constraints:
        constraint._state[ctx.id] = {"sentinel": True}
    agent._pending_step_tokens["current-task"] = 123

    agent._reset_turn_budget(ctx)

    assert all(ctx.id not in constraint._state for constraint in agent.constraints)
    assert agent._pending_step_tokens == {}


def test_full_job_output_read_acknowledges_one_subscriber_turn(monkeypatch):
    from agentevolver.runtime import runtime_manager

    ref = SimpleNamespace(
        alive=True,
        busy=False,
        turns=2,
        _tasks=SimpleNamespace(empty=lambda: True),
    )
    monkeypatch.setattr(
        runtime_manager, "child", lambda job_id: ref if job_id == "user-job" else None
    )
    contract = {"subscriber_job_ids": ["user-job"], "collected_turns": {}}
    ctx = SimpleNamespace(extra={"website_runtime_contract": contract})

    assert (
        JobEnvironment._record_subscriber_collection(
            "user-job",
            ctx,
            full=False,
        )
        == 0
    )
    assert (
        JobEnvironment._record_subscriber_collection(
            "user-job",
            ctx,
            full=True,
        )
        == 2
    )
    assert contract["collected_turns"] == {"user-job": 2}


def test_next_deploy_waits_until_every_subscriber_feedback_is_read(monkeypatch):
    from agentevolver.runtime import runtime_manager

    ready = SimpleNamespace(
        alive=True,
        busy=False,
        turns=1,
        last_turn_success=True,
        turn_results={1: "user feedback"},
        _tasks=SimpleNamespace(empty=lambda: True),
    )
    acceptance = SimpleNamespace(
        **{
            **ready.__dict__,
            "turn_results": {1: "VERDICT: PASS\nAll journeys passed."},
        }
    )
    monkeypatch.setattr(
        runtime_manager,
        "child",
        lambda job_id: acceptance if job_id == "acceptance" else ready,
    )
    contract = {
        "subscriber_job_ids": ["user-1", "acceptance"],
        "collected_turns": {"user-1": 1},
    }
    ctx = SimpleNamespace(
        extra={
            "website_runtime_contract": contract,
            "deployment_release_history": [{"release_number": 1}],
        }
    )

    assert "acceptance" in DeployTool._previous_release_blocker(ctx)
    contract["collected_turns"]["acceptance"] = 1
    assert DeployTool._previous_release_blocker(ctx) == ""


def test_next_release_does_not_require_an_evolution_decision(monkeypatch):
    from agentevolver.runtime import runtime_manager

    ready = SimpleNamespace(
        alive=True,
        busy=False,
        turns=1,
        last_turn_success=True,
        _tasks=SimpleNamespace(empty=lambda: True),
    )
    monkeypatch.setattr(runtime_manager, "child", lambda _job_id: ready)
    contract = {
        "subscriber_job_ids": ["user"],
        "collected_turns": {"user": 1},
        "evolution_decisions": [],
    }
    ctx = SimpleNamespace(
        extra={
            "website_runtime_contract": contract,
            "deployment_release_history": [{"release_number": 1}],
        }
    )

    assert DeployTool._previous_release_blocker(ctx) == ""


@pytest.mark.asyncio
async def test_keep_decision_requires_real_change_and_evaluation(monkeypatch):
    from agentevolver.extension import extension_manager

    component = SimpleNamespace(version="1.0.0")
    manifest = SimpleNamespace(find=lambda module, name: component)
    monkeypatch.setattr(extension_manager, "read_manifest", lambda: manifest)
    contract = {
        "evolution_runs": [
            {
                "agent": "generate_agent",
                "module": "skill",
                "name": "adaptive_ui",
                "version": "1.0.0",
                "success": True,
            },
            {
                "agent": "evaluate_agent",
                "module": "skill",
                "name": "adaptive_ui",
                "version": "1.0.0",
                "success": True,
            },
        ],
        "evolution_decisions": [],
    }
    response = await EvolutionTool()(
        action="record_decision",
        release_number=1,
        decision="keep",
        module="skill",
        name="adaptive_ui",
        evidence="Repeated measured gap.",
        evaluation="Candidate passed the baseline comparison.",
        ctx=SimpleNamespace(
            extra={
                "website_runtime_contract": contract,
                "deployment_release_history": [{"release_number": 1}],
            }
        ),
    )

    assert response.success is True
    assert contract["evolution_decisions"][0]["decision"] == "keep"


@pytest.mark.asyncio
async def test_builder_completion_requires_release_feedback_collection(monkeypatch, tmp_path):
    from agentevolver.runtime import runtime_manager

    ready = SimpleNamespace(
        alive=True,
        busy=False,
        turns=1,
        last_turn_success=True,
        turn_results={1: "user feedback"},
        _tasks=SimpleNamespace(empty=lambda: True),
    )
    acceptance = SimpleNamespace(
        **{
            **ready.__dict__,
            "turn_results": {1: "VERDICT: PASS\nAll journeys passed."},
        }
    )
    monkeypatch.setattr(
        runtime_manager,
        "child",
        lambda job_id: acceptance if job_id == "acceptance" else ready,
    )
    contract = {
        "required_releases": 1,
        "subscriber_job_ids": ["user-1", "acceptance"],
        "acceptance_job_id": "acceptance",
        "collected_turns": {"user-1": 1},
    }
    ctx = SimpleNamespace(
        extra={
            "website_runtime_contract": contract,
            "deployment_release_history": [{"source_revision": "one", "fanout": 2}],
        }
    )
    builder = WebsiteBuilderAgent(base_dir=str(tmp_path))

    assert "acceptance" in await builder._completion_blocker(ctx)
    contract["collected_turns"]["acceptance"] = 1
    assert await builder._completion_blocker(ctx) is None

    acceptance.turn_results[1] = "VERDICT: FAIL\nCheckout is broken."
    assert "did not pass" in await builder._completion_blocker(ctx)


@pytest.mark.asyncio
async def test_builder_completion_only_closes_self_initiated_evolution(monkeypatch, tmp_path):
    from agentevolver.runtime import runtime_manager

    ready = SimpleNamespace(
        alive=True,
        busy=False,
        turns=1,
        last_turn_success=True,
        turn_results={1: "VERDICT: PASS\nPassed."},
        _tasks=SimpleNamespace(empty=lambda: True),
    )
    monkeypatch.setattr(runtime_manager, "child", lambda _job_id: ready)
    contract = {
        "required_releases": 1,
        "subscriber_job_ids": ["acceptance"],
        "acceptance_job_id": "acceptance",
        "collected_turns": {"acceptance": 1},
        "evolution_runs": [],
        "evolution_decisions": [],
    }
    ctx = SimpleNamespace(
        extra={
            "website_runtime_contract": contract,
            "deployment_release_history": [{"source_revision": "one", "fanout": 1}],
        }
    )
    builder = WebsiteBuilderAgent(base_dir=str(tmp_path))

    assert await builder._completion_blocker(ctx) is None

    contract["evolution_runs"] = [
        {
            "agent": "generate_agent",
            "module": "skill",
            "name": "adaptive_ui",
            "version": "1.0.0",
            "success": True,
        }
    ]
    assert "missing evaluation and decision" in await builder._completion_blocker(ctx)

    contract["evolution_runs"].append(
        {
            "agent": "evaluate_agent",
            "module": "skill",
            "name": "adaptive_ui",
            "version": "1.0.0",
            "success": True,
        }
    )
    assert "missing keep/rollback/unload decision" in await builder._completion_blocker(ctx)

    contract["evolution_decisions"] = [
        {
            "release_number": 1,
            "decision": "keep",
            "module": "skill",
            "name": "adaptive_ui",
            "version": "1.0.0",
        }
    ]
    assert await builder._completion_blocker(ctx) is None


def test_builder_requires_verification_at_the_exact_deployed_url():
    prompt = (
        Path(__file__).resolve().parents[1]
        / "agentevolver"
        / "prompt"
        / "default"
        / "website_builder_agent.html"
    ).read_text(encoding="utf-8")

    assert "exact URL and source revision" in prompt
    assert "including any path prefix" in prompt
    assert "Never substitute a direct source port" in prompt


def test_website_demo_mounts_only_distinct_agents_tools_and_skills():
    from mmengine import Config

    cfg = Config.fromfile(
        str(Path(__file__).resolve().parents[1] / "configs" / "website_evolution_demo.py")
    )

    assert cfg.agent_names == [
        "website_builder_agent",
        "browser_agent",
        "generate_agent",
        "optimize_agent",
        "evaluate_agent",
        "website_user_agent",
    ]
    assert cfg.tool_names == [
        "bash_tool",
        "apply_patch_tool",
        "inspect_tool",
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


def test_website_demo_separates_release_acceptance_from_user_codesign():
    prompt_dir = Path(__file__).resolve().parents[1] / "agentevolver" / "prompt" / "default"
    builder = (prompt_dir / "website_builder_agent.html").read_text(encoding="utf-8")
    user = (prompt_dir / "website_user_agent.html").read_text(encoding="utf-8")
    normalized_builder = " ".join(builder.split())

    assert "Release acceptance and user co-design are separate evidence streams" in builder
    assert "exact URL" in builder
    assert "do not use them as the release test team" in normalized_builder
    assert "collaboration goal" in user


def test_website_task_manifest_routes_independent_acceptance(tmp_path):
    from examples.run_website_evolution_demo import build_task_text

    scenario = tmp_path / "scenario.html"
    scenario.write_text("<html><body><main>Build a site.</main></body></html>", encoding="utf-8")
    personas = []
    for index in range(1, 4):
        path = tmp_path / f"persona_{index:02d}.html"
        path.write_text(f"<html><body>User {index}</body></html>", encoding="utf-8")
        personas.append(path)

    task = build_task_text(scenario, personas)
    manifest = json.loads(task[task.index("{", task.index("runtime-input-manifest")) :])

    assert manifest["release_acceptance"] == {
        "agent": "browser_agent",
        "model": "llm_hub/gpt-5.6-sol",
        "after_initial_build": True,
        "after_each_optimization": True,
        "exact_deployed_url_only": True,
        "independent_from_user_codesign": True,
    }
    assert manifest["codesign_policy"]["participants_are_evaluators"] is False
    assert manifest["run_policy"] == {"blind_initial_build": True}
    assert "minimum_kept_evolutions" not in manifest
    assert all("source_path" not in item for item in manifest["attachments"])
    assert all(str(path) not in task for path in personas)


def test_website_prompts_do_not_encode_one_demo_protocol():
    prompt_dir = Path(__file__).resolve().parents[1] / "agentevolver" / "prompt" / "default"
    text = "\n".join(
        (prompt_dir / name).read_text(encoding="utf-8")
        for name in ("website_builder_agent.html", "website_user_agent.html")
    )
    for fixed_demo_term in (
        "V0",
        "V1",
        "V5",
        "exactly five",
        "website_evolution_demo",
        "feedback_ledger.json",
        "preference_ledger.json",
    ):
        assert fixed_demo_term not in text
