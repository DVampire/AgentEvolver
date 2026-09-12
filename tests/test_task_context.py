"""Public task projection keeps private routes out of both model input channels."""

import copy
import json
from types import SimpleNamespace

import pytest

from agentevolver.task.context import bind_manifest, parse_manifest, public_manifest, render_manifest, resolve_task


def test_runtime_defaults_bind_inputs_without_mutating_configuration(tmp_path):
    defaults = {"evolution": {"required_module_counts": {"environment": 2}}, "subscribers": []}
    original = copy.deepcopy(defaults)
    args = SimpleNamespace(task="Compare historical signals.", attach=["/inputs/study.json"])
    content, files, metadata = resolve_task(args, str(tmp_path), manifest_defaults=defaults)
    before, _, manifest = bind_manifest(content, ["/staged/study.json"])
    assert before == args.task
    assert manifest["attachments"] == [{"id": "input_0", "role": "requirements",
                                        "name": "study.json", "path": "/staged/study.json", "staged": True}]
    assert files == args.attach and metadata is None
    manifest["evolution"]["required_module_counts"]["environment"] = 3
    assert defaults == original


def test_authored_manifest_overrides_config_by_whole_section(tmp_path):
    explicit = {"attachments": [{"id": "answer", "role": "private"}],
                "deployment": {"required_releases": 1}, "evolution": {"require_verified_improvement": False}}
    args = SimpleNamespace(task=render_manifest("Review", "Input roles", explicit), attach=["/answer"])
    content, _, _ = resolve_task(args, str(tmp_path), manifest_defaults={
        "deployment": {"required_releases": 2, "topic": "demo.ready"},
        "evolution": {"required_module_counts": {"environment": 2}},
        "run_policy": {"self_review": True},
    })
    manifest = parse_manifest(content)[2]
    assert manifest["deployment"] == explicit["deployment"]
    assert manifest["evolution"] == explicit["evolution"]
    assert manifest["attachments"] == explicit["attachments"]
    assert manifest["run_policy"] == {"self_review": True}


def test_resolving_ordinary_tasks_keeps_previous_behavior(tmp_path):
    args = SimpleNamespace(task="Hello", attach=["notes.md"])
    assert resolve_task(args, str(tmp_path)) == ("Hello", ["notes.md"], None)
    assert resolve_task(args, str(tmp_path), manifest_defaults={}) == ("Hello", ["notes.md"], None)
    with pytest.raises(ValueError, match="task_manifest_defaults"):
        resolve_task(args, str(tmp_path), manifest_defaults=["environment"])


def test_mismatched_config_attachment_declarations_fail_before_submission(tmp_path):
    with pytest.raises(ValueError, match="attachment count"):
        resolve_task(SimpleNamespace(task="Review", attach=[]), str(tmp_path), manifest_defaults={
            "attachments": [{"id": "missing", "role": "requirements"}],
        })


def test_public_manifest_supports_domain_roles_without_mutating_private_input():
    manifest = {
        "attachments": [
            {"id": "spec", "role": "specification", "path": "/staged/spec.md"},
            {"id": "key", "role": "answer_key", "path": "/private/key.md", "staged": True},
        ],
        "reviewer": {"model": "private-route"},
    }
    updates = {"reviewer": {"job_id": "review-1"}}
    original = copy.deepcopy(manifest)

    task, files = public_manifest(
        "Solve the problem.", "Input routing.", manifest,
        private_roles={"answer_key"}, updates=updates,
    )
    projected = json.loads(task[task.index("{"):])

    assert "/private/key.md" not in task
    assert "private-route" not in task
    assert files == ["/staged/spec.md"]
    assert projected["attachments"][1] == {
        "id": "key", "role": "answer_key", "routing": "runtime_private",
    }
    assert projected["reviewer"] == {"job_id": "review-1"}
    assert manifest == original
    assert updates == {"reviewer": {"job_id": "review-1"}}


def test_public_manifest_filters_private_paths_even_in_updated_attachments():
    task, files = public_manifest(
        "Task", "", {"attachments": []}, private_roles={"private"},
        updates={"attachments": [{"id": "secret", "role": "private", "path": "/secret"}]},
    )
    assert "/secret" not in task
    assert files == []


def test_manifest_rejects_ambiguous_attachment_ids():
    task = render_manifest("Task", "", {"attachments": [
        {"id": "shared", "role": "requirements"},
        {"id": "shared", "role": "user_context"},
    ]})
    with pytest.raises(ValueError, match="duplicated"):
        bind_manifest(task, ["/spec", "/persona"])


@pytest.mark.asyncio
async def test_base_agent_prepares_public_task_through_the_common_interface():
    from agentevolver.agent.loop.agent import Agent

    task = render_manifest("Review the solution.", "", {"attachments": [
        {"id": "solution", "role": "submission"},
        {"id": "key", "role": "answer_key"},
    ]})
    ctx = SimpleNamespace(extra={})
    public_task, public_files = await Agent().prepare_task(
        task, ["/staged/solution", "/staged/key"], ctx,
        private_roles=["answer_key"], manifest_updates={"reviewer": {"job_id": "r1"}},
    )
    assert "/staged/key" not in public_task
    assert public_files == ["/staged/solution"]
    assert ctx.extra["task_files"] == public_files
    assert json.loads(public_task[public_task.index("{"):])["reviewer"] == {"job_id": "r1"}
    assert await Agent().prepare_task("Ordinary task", ["brief.md"], ctx) == (
        "Ordinary task", ["brief.md"],
    )


@pytest.mark.asyncio
async def test_generic_agent_can_configure_subscribers_and_release_policy(tmp_path, monkeypatch):
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.deploy import deployment_manager
    from agentevolver.deploy.types import SiteRecord
    from agentevolver.runtime import kernel
    from agentevolver.tool.default.deployment.deploy import DeployTool

    secret = tmp_path / "review.md"
    secret.write_text("Independent reference checks")
    task = render_manifest("Build an API", "", {
        "attachments": [{"id": "reference", "role": "review_only"}],
        "private_attachment_roles": ["review_only"],
        "subscribers": [{"id": "api-review", "agent": "reviewer_agent",
                         "brief": {"task": "Check API compatibility", "model": "private-route",
                                   "subscription_topics": ["api.ready"]},
                         "attachments": ["reference"]}],
        "deployment": {"topic": "api.ready", "required_releases": 1,
                       "acceptance_subscriber": "api-review"},
    })
    calls = []

    async def dispatch(name, brief, **kwargs):
        calls.append((name, brief))
        return SimpleNamespace(pid="api-review-job")

    async def publish(topic, event_type, payload, **kwargs):
        assert topic == event_type == "api.ready"
        return 1, "root::api.ready", SimpleNamespace(id="event-1")

    monkeypatch.setattr(type(kernel), "dispatch", lambda self, *a, **kw: dispatch(*a, **kw))
    monkeypatch.setattr(kernel, "publish_scoped", publish)
    monkeypatch.setattr(kernel, "get", lambda pid: SimpleNamespace(turns=0))
    ctx = SimpleNamespace(extra={})
    text, files = await Agent().prepare_task(task, [str(secret)], ctx)
    assert calls[0][0] == "reviewer_agent"
    assert "Independent reference checks" in calls[0][1]["task"]
    assert "Independent reference checks" not in text and str(secret) not in text
    assert "private-route" not in text and files == []
    assert "api-review-job" in text
    assert not ctx.extra["task_state"]
    status = await DeployTool()(action="status", ctx=ctx)
    assert status.success and status.data["subscription"]["topic"] == "api.ready"
    receipt = await deployment_manager.publish_release(
        SiteRecord(site_id="api", runtime="python", source_revision="revision-1"),
        action="deploy", ctx=ctx,
    )
    assert receipt["topic"] == "api.ready"
    assert receipt["subscriber_min_turns"] == {"api-review-job": 1}


@pytest.mark.asyncio
async def test_deploy_policy_binds_only_on_tool_use_and_state_survives_context_conversion():
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.agent.types import AgentContext
    from agentevolver.runtime.kernel import child_context
    from agentevolver.tool.default.deployment.deploy import DeployTool
    from agentevolver.tool.types import ToolContext

    ctx = AgentContext()
    agent = Agent()
    task = render_manifest("Build a site", "", {
        "attachments": [], "deployment": {"required_releases": 1},
    })
    await agent.prepare_task(task, [], ctx)
    assert ctx.extra["task_state"] == {}
    tool = DeployTool()
    result = await tool(action="status", ctx=ToolContext.from_context(ctx))
    assert result.success and result.data["ready"] is False
    history = ctx.extra["task_state"]["deployment_release_history"]
    history.append({"source_revision": "r1", "fanout": 0})
    # A new ToolContext for each call used to lose lazily-created top-level state.
    result = await tool(action="status", ctx=ToolContext.from_context(ctx))
    assert result.success and result.data["ready"] is True
    assert result.data["completed_releases"] == 1
    assert ctx.extra["task_state"]["deployment_release_history"] is history
    assert not tool.will_mutate({"action": "status"})

    child = child_context(Agent(), {}, agent, ctx)
    assert "task_state" not in child.extra and "task_manifest" not in child.extra
    result = await tool(action="status", ctx=ToolContext.from_context(child))
    assert result.success and result.data["configured"] is False
    assert result.data["ready"] is None  # No policy is not a verified deployment.
