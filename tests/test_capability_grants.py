"""An allowlist is a default that a grant can widen — including mid-run.

`capability_allowlists` is a class field, so it states an isolation contract: a website
visitor holds `done_tool` and eleven browser actions and must not reach the workspace.
That part is right. What was wrong is that nothing could ever widen it:

- The dispatch schema has declared `tool_allowlist`, `skill_allowlist`,
  `connector_allowlist` and `plugin_allowlist` for as long as it has existed, and no code
  read them. A parent narrowing or widening a child's roster was silently ignored.
- `schemas()` copied the class field into the context with `setdefault`, which freezes on
  first use: the default occupied the key from step 0, so a grant made later could not get
  in even if something had made one.
- The capability catalog was cached against the extension registry's revision alone, so a
  scope change was invisible until an unrelated registration happened to bump it.

Together those made the one consumer that genuinely lacked a capability — an agent with no
shell, which is why the gap was real — also the one that could never be given it. A run
could evolve a component for it and have nowhere to put it.
"""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agentevolver.agent.loop.router import GRANTED_ALLOWLISTS, CapabilityRouter, grant
from agentevolver.agent.types import AgentContext


def _isolated_agent():
    """An agent whose class field states a locked-down roster."""
    return SimpleNamespace(
        name="visitor",
        capability_allowlists={"tool": ["done_tool"]},
        defer_capabilities_after=40,
        env_names=[],
    )


@pytest_asyncio.fixture
async def router():
    from agentevolver.tool import tool_manager

    await tool_manager.initialize(["done_tool", "inspect_tool", "bash_tool"])
    return CapabilityRouter()


async def _tools(router, agent, ctx):
    _schemas, routing = await router.schemas(agent, ctx)
    return sorted(name for name, route in routing.items() if route[0] == "tool")


def _ctx(session: str):
    from agentevolver.session import SessionContext

    ctx = SessionContext(id=session)
    ctx.extra = {}
    return ctx


# ---------------------------------------------------------------------------
# The dispatch grant
# ---------------------------------------------------------------------------


def test_a_dispatch_grant_reaches_the_child():
    """The schema declared these; nothing read them. Same shape as `target_type`."""
    child = CapabilityRouter._child_context(
        {
            "task": "verify the release",
            "tool_allowlist": ["done_tool", "media_probe_tool"],
            "skill_allowlist": [],
        },
        SimpleNamespace(name="builder"),
        SimpleNamespace(id="parent", extra={}),
    )
    assert child.extra["tool_allowlist"] == ["done_tool", "media_probe_tool"]
    assert child.extra["skill_allowlist"] == [], "an empty grant means 'none of this kind'"
    assert "tool_allowlist" in child.extra[GRANTED_ALLOWLISTS]


def test_an_ordinary_dispatch_grants_nothing():
    """Absent a grant the child keeps its own class default, whatever that is."""
    child = CapabilityRouter._child_context(
        {"task": "read the log"}, SimpleNamespace(name="meta"),
        SimpleNamespace(id="parent", extra={}),
    )
    assert "tool_allowlist" not in child.extra
    assert GRANTED_ALLOWLISTS not in child.extra


# ---------------------------------------------------------------------------
# Default versus grant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_class_field_applies_when_nothing_was_granted(router):
    agent, ctx = _isolated_agent(), _ctx("default-only")
    assert await _tools(router, agent, ctx) == ["done_tool"]


@pytest.mark.asyncio
async def test_a_default_is_refreshed_rather_than_frozen(router):
    """`setdefault` froze the default into the context on step 0.

    Which is why a grant made on step 5 could not take effect: the key was already
    occupied by the default that the grant was meant to replace.
    """
    agent, ctx = _isolated_agent(), _ctx("refresh")
    await router.schemas(agent, ctx)
    agent.capability_allowlists = {"tool": ["done_tool", "bash_tool"]}
    assert await _tools(router, agent, ctx) == ["bash_tool", "done_tool"]


@pytest.mark.asyncio
async def test_a_grant_survives_the_next_step(router):
    """A grant must not be overwritten by the class field it is widening."""
    agent, ctx = _isolated_agent(), _ctx("grant-persists")
    assert await _tools(router, agent, ctx) == ["done_tool"]

    ctx.extra["tool_allowlist"] = ["done_tool", "inspect_tool"]
    grant(ctx.extra, "tool_allowlist")

    assert await _tools(router, agent, ctx) == ["done_tool", "inspect_tool"]
    assert await _tools(router, agent, ctx) == ["done_tool", "inspect_tool"], (
        "a grant that lasts one step is not a grant"
    )


@pytest.mark.asyncio
async def test_a_scope_change_rebuilds_the_catalog(router):
    """The catalog was keyed on the registry revision alone.

    So a grant was invisible until some unrelated registration moved that number.
    """
    agent, ctx = _isolated_agent(), _ctx("cache-key")
    await router.schemas(agent, ctx)
    before = dict(ctx.extra.get("_capability_catalog_revisions") or {})

    ctx.extra["tool_allowlist"] = ["done_tool", "inspect_tool"]
    grant(ctx.extra, "tool_allowlist")
    await router.schemas(agent, ctx)

    assert ctx.extra["_capability_catalog_revisions"] != before


# ---------------------------------------------------------------------------
# Granting to a process that is already running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_live_process_can_be_granted_a_capability(monkeypatch):
    """The case the whole mechanism exists for.

    A subscriber spawned at run start cannot be re-dispatched with a wider roster — it is
    resident, and the component it needs did not exist when it started. Its grant has to
    reach it while it runs.
    """
    from agentevolver.runtime.kernel import Kernel
    from agentevolver.tool import tool_manager
    from agentevolver.tool.default.evolution import EvolutionTool

    await tool_manager.initialize(["done_tool", "inspect_tool"])

    class Visitor:
        name = "visitor"
        capability_allowlists = {"tool": ["done_tool"]}
        defer_capabilities_after = 40
        env_names: list = []

        def __init__(self):
            self.seen: list = []

        async def __call__(self, task, files=None, ctx=None, **kwargs):
            router = CapabilityRouter()
            for _ in range(8):
                await self.proc.gate()
                _schemas, routing = await router.schemas(self, ctx)
                self.seen.append(
                    sorted(n for n, r in routing.items() if r[0] == "tool")
                )
                await asyncio.sleep(0.03)
            return "done"

        async def on_event(self, envelope, proc):
            pass

    kernel = Kernel()
    agent = Visitor()
    try:
        proc = await kernel.spawn(agent, "browse", ctx=AgentContext(name="v", extra={}))
        await asyncio.sleep(0.08)
        assert agent.seen[-1] == ["done_tool"]

        from agentevolver.runtime import kernel as live_kernel

        monkeypatch.setattr(live_kernel, "get", lambda pid: kernel.get(pid))
        result = await EvolutionTool()(
            action="grant", job_id=proc.pid, module="tool", name="inspect_tool",
        )
        assert result.success, result.message

        await asyncio.sleep(0.15)
        assert agent.seen[-1] == ["done_tool", "inspect_tool"]
    finally:
        await kernel.shutdown(timeout=5)


@pytest.mark.asyncio
async def test_granting_to_a_process_that_is_not_there_is_refused(monkeypatch):
    """A grant names a live process; a wrong pid must say so, not pass silently."""
    from agentevolver.runtime import kernel as live_kernel
    from agentevolver.tool.default.evolution import EvolutionTool

    monkeypatch.setattr(live_kernel, "get", lambda pid: None)
    result = await EvolutionTool()(
        action="grant", job_id="no-such-pid", module="tool", name="inspect_tool",
    )
    assert result.success is False
    assert "no live process" in result.message
