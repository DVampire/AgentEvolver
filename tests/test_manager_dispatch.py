"""A capability manager is called the way its own signature declares.

The router routes seven capability kinds to three destinations, and the managers behind
them do not share one signature: `environment_manager.__call__(name, action, input, ctx)`
requires `action` positionally, `connector` and `plugin` take it as an optional keyword,
and `skill` and `workflow` do not take it at all.

The router put `action` *inside* `input` instead of passing it as the parameter it is.
Every environment action therefore raised
`TypeError: EnvironmentManagerServer.__call__() missing 1 required positional argument:
'action'` — `job__output`, `job__wait`, `job__kill` and every browser action, dead.

It survived the suite because no test drove a real manager through the router: the loop
tests supply a stub router, and the environment tests call the manager directly with the
right shape. It surfaced only in a live run, inside an evaluate_agent's report: "The same
failure occurs in my process. All five calls raised the identical error."
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio

from agentevolver.agent.loop.decision import ActionCall
from agentevolver.agent.loop.router import CapabilityRouter


@pytest_asyncio.fixture
async def wired():
    """A router over the real registries, scoped to the job environment."""
    from agentevolver.environment import environment_manager
    from agentevolver.session import SessionContext
    from agentevolver.tool import tool_manager

    await environment_manager.initialize(["job"])
    await tool_manager.initialize(["bash_tool", "done_tool"])
    ctx = SessionContext(id="manager-dispatch-probe")
    ctx.extra = {}
    agent = SimpleNamespace(
        name="probe", capability_allowlists={}, defer_capabilities_after=40,
        env_names=["job"],
    )
    router = CapabilityRouter()
    _tools, routing = await router.schemas(agent, ctx)
    return router, agent, ctx, routing


@pytest.mark.asyncio
async def test_the_environment_projects_its_actions_as_callables(wired):
    """Guards the guard: with no environment routes the assertions below prove nothing."""
    _router, _agent, _ctx, routing = wired
    actions = sorted(n for n, route in routing.items() if route[0] == "environment")
    assert "job__list" in actions, f"job actions missing from the roster: {actions}"


@pytest.mark.asyncio
async def test_an_environment_action_reaches_its_manager(wired):
    """The call must land in the environment, not die on the manager's signature.

    Asserted on the *shape* of the answer rather than its content: what matters is that
    a TypeError about `action` is gone and the environment itself replied.
    """
    router, agent, ctx, routing = wired
    result = await router.invoke(
        ActionCall(id="c1", name="job__list", args={}),
        agent=agent, ctx=ctx, routing=routing,
    )
    assert not result.error, result.error
    assert "job" in str(result.output).lower()


@pytest.mark.asyncio
async def test_a_bad_argument_comes_back_as_the_environment_s_own_refusal(wired):
    """A domain error proves the call arrived; a TypeError proves it never did."""
    router, agent, ctx, routing = wired
    result = await router.invoke(
        ActionCall(id="c2", name="job__output", args={"job_id": "no-such-job"}),
        agent=agent, ctx=ctx, routing=routing,
    )
    assert result.error, "an unknown job id must be refused"
    assert "no-such-job" in result.error or "No job" in result.error
    assert "positional argument" not in result.error
    assert "TypeError" not in result.error


@pytest.mark.asyncio
async def test_action_is_not_smuggled_into_the_action_s_own_arguments(wired):
    """`action` addresses the manager; it is not one of the action's parameters.

    Leaving it in `input` also hands every environment action an argument it never
    declared, which a strict signature check would reject.
    """
    router, agent, ctx, routing = wired
    seen = {}

    class Spy:
        async def __call__(self, name, action="", input=None, ctx=None, **kwargs):
            seen.update(name=name, action=action, input=dict(input or {}))
            from agentevolver.response.types import Response, ResponseType
            return Response(type=ResponseType.ENVIRONMENT, success=True, message="ok")

    import agentevolver.capability as capability
    entry = next(e for e in capability.MOUNTED_TYPES if e.type == "environment")
    original = entry.manager
    object.__setattr__(entry, "manager", lambda: Spy())
    try:
        await router.invoke(
            ActionCall(id="c3", name="job__output", args={"job_id": "abc"}),
            agent=agent, ctx=ctx, routing=routing,
        )
    finally:
        object.__setattr__(entry, "manager", original)

    assert seen["action"] == "output", seen
    assert seen["input"] == {"job_id": "abc"}, seen
    assert "action" not in seen["input"]
