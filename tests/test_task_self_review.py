"""Browser review provenance and repair at both completion entry points."""
from types import SimpleNamespace

import pytest

from agentevolver.agent.loop import ActionCall, ActionResult, Decision
from agentevolver.task.self_review import blocker, observe_actions, observe_state


def release(number, revision=None):
    return {"release_url": f"http://review.test/s/report--r{number}/",
            "source_revision": revision or str(number)}


def context(*releases):
    return SimpleNamespace(id="review", extra={
        "task_manifest": {"run_policy": {"self_review": True}},
        "task_state": {"deployment_release_history": list(releases)},
    })


def observation(target):
    return {"state": "Rendered page", "extra": {"url": target["release_url"] + "#chart"}}


def action(call_id, target, *, name="command", error=""):
    return ActionResult(call=ActionCall(id=call_id, name=name, args={}), error=error,
                        extra={"extra": {"browser_url": target["release_url"]}})


ROUTING = {name: ("environment", "browser_environment", name)
           for name in ("command", "goto", "type", "click")}


def test_navigation_and_interaction_belong_to_actual_destination():
    old, new = release(1), release(2)
    ctx = context(old, new)
    observe_state(ctx, observation(old))
    observe_actions(ctx, [action("navigate-and-click", new)], ROUTING, observation(old))
    assert blocker(ctx, new)  # A later state observation is still required.
    observe_state(ctx, observation(new))
    assert not blocker(ctx, new)
    assert blocker(ctx, old)
    assert blocker(ctx, {**new, "source_revision": "changed"})


def test_batch_destinations_failures_and_successful_repair_stay_separate():
    a, b = release(1), release(2)
    ctx = context(a, b)
    observe_actions(ctx, [action("a", a), action("b", b)], ROUTING, observation(a))
    observe_state(ctx, observation(b))
    assert blocker(ctx, a) and not blocker(ctx, b)
    observe_actions(ctx, [action("failed-b", b, error="click timed out")], ROUTING, observation(a))
    observe_state(ctx, observation(b))
    assert blocker(ctx, b)
    observe_actions(ctx, [action("repair-b", b, name="type")], ROUTING, observation(a))
    observe_state(ctx, observation(b))
    assert not blocker(ctx, b)


def test_navigation_only_missing_provenance_and_other_routes_do_not_pass():
    target = release(1)
    for result, routing in [
        (action("nav", target, name="goto"), ROUTING),
        (ActionResult(call=ActionCall(id="missing", name="command", args={}),
                      output=target["release_url"]), ROUTING),
        (action("other", target), {"command": ("tool", "command")}),
        (action("other-site", release(10)), ROUTING),
    ]:
        ctx = context(target)
        observe_state(ctx, observation(target))
        observe_actions(ctx, [result], routing, observation(target))
        observe_state(ctx, observation(target))
        assert blocker(ctx, target)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["text", "done_tool", "same_turn"])
async def test_completion_returns_missing_review_to_agent(monkeypatch, ending):
    from test_agent_loop import Scripted, StubRouter, calls
    from agentevolver.deploy import deployment_manager

    target = release(1)
    ctx = context(target)
    monkeypatch.setattr(deployment_manager, "prepare_task", lambda ctx: None)
    monkeypatch.setattr(deployment_manager, "release_status", lambda ctx: {"ready": True, "completed_releases": 1})

    class Router(StubRouter):
        async def schemas(self, agent, ctx):
            return [], {**ROUTING, "done_tool": ("tool", "done_tool")}

        async def invoke(self, call, **kwargs):
            if call.name == "done_tool":
                return ActionResult(call=call, output="Finished", final=True)
            return ActionResult(call=call, output="Clicked", extra={"extra": {"browser_url": target["release_url"]}})

    class ReviewingAgent(Scripted):
        async def environment_state(self, ctx):
            self._environment_observations = {"browser_environment": observation(target)}
            return "Current report"

    final = Decision(text="Finished") if ending == "text" else calls(("done_tool", {}))
    script = ([calls(("command", {}), ("done_tool", {}))] if ending == "same_turn"
              else [final, calls(("command", {})), final])
    agent = ReviewingAgent(script, router=Router({}), max_step=5)
    response = await agent("Review and finish", ctx=ctx)
    assert response.success
    assert len(agent.seen_live) == (1 if ending == "same_turn" else 3)
    if ending != "same_turn":
        assert any("Completion deferred" in block for block in agent.seen_live[1])
    assert response.data["self_review_status"]["ready"]


@pytest.mark.asyncio
async def test_repeated_unsupported_endings_fail_instead_of_spinning(monkeypatch):
    from test_agent_loop import Scripted, StubRouter
    from agentevolver.task import self_review

    monkeypatch.setattr(self_review, "status", lambda ctx: {"required": True, "ready": False, "reasons": ["review missing"]})
    agent = Scripted([Decision(text="Finished")] * 8, router=StubRouter({}), max_step=8)
    response = await agent("Finish", ctx=context())
    assert not response.success
    assert len(agent.seen_live) == 4
