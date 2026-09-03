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
    from agentevolver.tool.default.coordination.grant import GrantTool

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
        result = await GrantTool()(
            job_id=proc.pid, module="tool", name="inspect_tool",
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
    from agentevolver.tool.default.coordination.grant import GrantTool

    monkeypatch.setattr(live_kernel, "get", lambda pid: None)
    result = await GrantTool()(
        job_id="no-such-pid", module="tool", name="inspect_tool",
    )
    assert result.success is False
    assert "No live process" in result.message


# ---------------------------------------------------------------------------
# Declared acceptance: the path that needs no edge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_declared_type_admits_components_registered_during_the_run(router, monkeypatch):
    """The path that reaches a subscriber, which no grant can address.

    A publisher does not know who listens — that is what the indirection is for — so a
    component evolved mid-run cannot be handed to a listening process by name. Declaring
    a type in advance needs no edge at all.
    """
    from agentevolver.extension import extension_manager
    from agentevolver.extension.types import Manifest, ManifestComponent

    monkeypatch.setattr(
        extension_manager, "read_manifest",
        lambda: Manifest(components=[
            ManifestComponent(module="tool", name="inspect_tool",
                              version="1.0.0", file="tool/inspect_tool.py"),
        ]),
    )
    closed = _isolated_agent()
    closed.accepts_evolved = []
    opened = _isolated_agent()
    opened.accepts_evolved = ["tool"]

    assert await _tools(router, closed, _ctx("closed")) == ["done_tool"]
    assert await _tools(router, opened, _ctx("opened")) == ["done_tool", "inspect_tool"]


@pytest.mark.asyncio
async def test_acceptance_does_not_widen_a_type_that_was_not_declared(router, monkeypatch):
    """A browser agent accepts tools and not skills, and the difference has to hold.

    A skill would let it read the source instead of using the product, which is the one
    thing that makes its account of the product worth having.
    """
    from agentevolver.extension import extension_manager
    from agentevolver.extension.types import Manifest, ManifestComponent

    monkeypatch.setattr(
        extension_manager, "read_manifest",
        lambda: Manifest(components=[
            ManifestComponent(module="skill", name="read_the_source_skill",
                              version="1.0.0", file="skill/read_the_source_skill"),
        ]),
    )
    agent = _isolated_agent()
    agent.capability_allowlists = {"tool": ["done_tool"], "skill": []}
    agent.accepts_evolved = ["tool"]

    ctx = _ctx("typed")
    await router.schemas(agent, ctx)
    assert ctx.extra["skill_allowlist"] == [], "an undeclared type stays closed"


@pytest.mark.asyncio
async def test_a_name_that_unloaded_is_skipped_rather_than_fatal(router):
    """A grant writes a name; the component behind it may be rolled back later.

    A roster is built from what is registered, so the dead name simply does not appear.
    Asserted because the alternative — a scope that raises once a component unloads —
    would take down every step of every process that had been granted it.
    """
    agent = _isolated_agent()
    ctx = _ctx("dead-name")
    ctx.extra["tool_allowlist"] = ["done_tool", "ghost_tool_that_was_unloaded"]
    grant(ctx.extra, "tool_allowlist")
    assert await _tools(router, agent, ctx) == ["done_tool"]

    ctx.extra["tool_allowlist"] = ["ghost_a", "ghost_b"]
    assert await _tools(router, agent, ctx) == [], "all-dead is empty, not an error"


def test_the_browser_roles_accept_tools_and_nothing_else():
    """The two roles that most need this, and the one type they may safely take."""
    import agentevolver.agent  # noqa: F401 - registers the actors
    from agentevolver.registry import AGENT

    for name in ("BrowserAgent", "WebsiteUserAgent"):
        agent = AGENT.get(name)(base_dir="")
        assert agent.accepts_evolved == ["tool"], name
        assert agent.capability_allowlists["tool"] == ["done_tool"], name
        assert agent.capability_allowlists["skill"] == [], name


def test_an_unrestricted_agent_declares_nothing():
    """An empty allowlist already means everything; declaring acceptance would be noise."""
    import agentevolver.agent  # noqa: F401 - registers the actors
    from agentevolver.registry import AGENT

    for name in ("MetaAgent", "CodeAgent", "WebsiteBuilderAgent"):
        agent = AGENT.get(name)(base_dir="")
        assert agent.accepts_evolved == [], name
        assert not agent.capability_allowlists.get("tool"), name


@pytest.mark.asyncio
async def test_a_listing_reports_grants_but_not_defaults():
    """`ps` should answer "who holds what this run evolved".

    Only grants: reporting a default as a grant would say every restricted agent had
    been granted its own restriction, which is the distinction the marker exists for.
    """
    from agentevolver.runtime.kernel import Kernel
    from agentevolver.runtime.modes import InteractionMode

    class Idler:
        name = "visitor"

        async def __call__(self, task, files=None, ctx=None, **kwargs):
            for _ in range(8):
                await self.proc.gate()
                await asyncio.sleep(0.03)
            return "ok"

        async def on_event(self, envelope, proc):
            pass

    kernel = Kernel()
    try:
        ctx = AgentContext(name="v", extra={})
        proc = await kernel.spawn(
            Idler(), "x", mode=InteractionMode.SERVICE, ctx=ctx,
        )
        await asyncio.sleep(0.05)
        assert proc.snapshot()["grants"] == {}

        ctx.extra["tool_allowlist"] = ["done_tool", "media_probe_tool"]
        grant(ctx.extra, "tool_allowlist")
        assert proc.snapshot()["grants"] == {
            "tool_allowlist": ["done_tool", "media_probe_tool"],
        }
    finally:
        await kernel.shutdown(timeout=5)
