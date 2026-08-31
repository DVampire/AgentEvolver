"""An agent that holds an environment can see inside it, whatever kind of agent it is.

The type table says an agent *uses* a capability, is *in* an environment and *has* a
memory. Two of those had a context method; `environment` borrowed
`_get_capability_context`, which asks a manager for its roster and nothing else. So the
base class could name an environment and never see into it, and the live state existed
only in `BrowserAgent` and `SSHAgent` — each overriding `_get_agent_context` to fetch it.

Which made "works in an environment" a property of two agent classes rather than of
holding an environment. Any other agent given a browser got a name and a list of actions,
and had to call one to find out what page was open.

`_get_environment_context` is the third method. It fills two slots for the same reason the
capability roster and the agent's state are separate blocks: a roster does not change
between steps and renders before the state, while the state changes every step and renders
after it.
"""

from __future__ import annotations

import contextlib
import io
from typing import Any, Dict

import pytest
import pytest_asyncio

from agentevolver.agent.types import Agent, AgentContext


class _Probe(Agent):
    """A plain agent: no environment overrides, nothing special about it.

    That is the point — the behaviour under test has to belong to the base class, and a
    fixture built on `BrowserAgent` would pass on that agent's own override.
    """

    name: str = "environment_probe"
    description: str = "Holds environments and nothing else."
    metadata: Dict[str, Any] = {}


@pytest_asyncio.fixture
async def probe():
    from agentevolver.environment.server import environment_manager

    with contextlib.redirect_stdout(io.StringIO()):
        await environment_manager.initialize()
    yield _Probe(base_dir=".")
    await environment_manager.cleanup()


def _ctx(*environments: str) -> AgentContext:
    ctx = AgentContext(id="environment-context-test", name="probe")
    ctx.extra["environment_allowlist"] = list(environments)
    return ctx


@pytest.mark.asyncio
async def test_the_base_class_has_the_third_context_method():
    """Named as its own method rather than reached through the capability one.

    `_get_capability_context` returns `available_<mount_type>` and nothing else; asking it
    about an environment is how the state came to be missing for eight of the ten agents.
    """
    assert hasattr(Agent, "_get_environment_context")
    assert hasattr(Agent, "_get_capability_context")
    assert hasattr(Agent, "_get_agent_context")


@pytest.mark.asyncio
async def test_an_agent_with_no_environment_gets_two_empty_slots(probe):
    """Empty, not a notice.

    Both templates guard their block with `{% if %}`, so an agent that works in nothing
    pays no prompt for the fact — and nothing is fetched, so it costs no round trip either.
    """
    slots = await probe._get_environment_context(_ctx())
    assert slots["environment_context"] == ""
    assert slots["environment_state"] == ""


@pytest.mark.asyncio
async def test_an_unreachable_environment_is_reported_in_place(probe):
    """A browser that will not start is a fact the agent needs, not a reason to fail.

    It is about to try to act in that environment. Raising here would lose the whole
    prompt build — turning a broken environment into a broken run — and returning nothing
    would let the agent plan against a state it never saw.
    """
    slots = await probe._get_environment_context(_ctx("no_such_env"))
    assert "no_such_env" in slots["environment_state"]
    assert "unavailable" in slots["environment_state"]


@pytest.mark.asyncio
async def test_every_mounted_environment_is_read_not_just_one(probe, monkeypatch):
    """An agent given two environments is in both.

    Deciding which one is "current" from the outside is guessing: an SSH state is a host
    and a workspace, a browser state is a page, and neither substitutes for the other. Each
    block is headed by name when there is more than one, so the agent can tell them apart.
    """
    from agentevolver.capability import ENVIRONMENT_TYPE

    manager = ENVIRONMENT_TYPE.manager()

    async def _state(env_name, ctx=None, **kwargs):
        return {"state": f"state of {env_name}"}

    monkeypatch.setattr(manager, "get_state", _state)

    body = (await probe._get_environment_context(_ctx("alpha", "beta")))["environment_state"]
    assert "state of alpha" in body and "state of beta" in body
    assert "#### alpha" in body and "#### beta" in body


@pytest.mark.asyncio
async def test_a_single_environment_is_not_given_a_heading(probe, monkeypatch):
    """A heading distinguishes; with one environment there is nothing to distinguish from,
    and the heading is prompt spent on saying so."""
    from agentevolver.capability import ENVIRONMENT_TYPE

    async def _state(env_name, ctx=None, **kwargs):
        return {"state": "the only state"}

    monkeypatch.setattr(ENVIRONMENT_TYPE.manager(), "get_state", _state)

    body = (await probe._get_environment_context(_ctx("solo")))["environment_state"]
    assert body == "the only state"


# --------------------------------------------------------------------------- #
# Where the slots are filled
# --------------------------------------------------------------------------- #
def test_the_specialised_agents_override_the_environment_method_not_the_agent_one():
    """Where they fetch state decides whether their value survives.

    Prompt assembly runs `_get_agent_context` first and the capability walk second, so a
    value set in the former is overwritten by the latter. `BrowserAgent` filled
    `environment_state` in `_get_agent_context`; once the base class filled it too, that
    value would be replaced — and replaced by a *second* screenshot of a page the step had
    already observed.
    """
    import inspect

    from agentevolver.agent.actor.browser_agent import BrowserAgent
    from agentevolver.agent.actor.ssh_agent import SSHAgent

    for cls in (BrowserAgent, SSHAgent):
        assert "_get_environment_context" in cls.__dict__, (
            f"{cls.__name__} must fill the slot in the method that runs last"
        )
        source = inspect.getsource(cls.__dict__["_get_agent_context"])
        assert 'base["environment_state"]' not in source, (
            f"{cls.__name__} still sets environment_state where it will be overwritten"
        )


def test_prompt_assembly_fills_the_state_after_the_agent_context():
    """The ordering the test above depends on, asserted rather than assumed."""
    import inspect

    source = inspect.getsource(Agent._get_messages)
    at_agent = source.index("_get_agent_context")
    at_capabilities = source.index("_capability_slots")
    assert at_agent < at_capabilities


# --------------------------------------------------------------------------- #
# Every prompt can render it
# --------------------------------------------------------------------------- #
def test_every_shipped_prompt_carries_the_environment_block():
    """A slot the base class fills and a template never renders is work thrown away.

    Three prompts had the block — the three whose agents fetched the state themselves. The
    other seven would have been handed a roster and a state with nowhere to put either.
    """
    from pathlib import Path

    prompts = Path(__file__).resolve().parents[1] / "agentevolver" / "prompt" / "default"
    missing = [p.name for p in sorted(prompts.glob("*.html"))
               if "module/environment_context.html" not in p.read_text(encoding="utf-8")]
    assert not missing, f"prompts with no environment-context block: {missing}"


def test_every_shipped_prompt_can_render_the_state():
    """The state slot lives in the shared `agent_context` module, so this is one check that
    every prompt includes that module rather than ten copies of a block."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "agentevolver" / "prompt"
    assert "{% if environment_state %}" in (root / "module" / "agent_context.html").read_text(
        encoding="utf-8")
    missing = [p.name for p in sorted((root / "default").glob("*.html"))
               if "module/agent_context.html" not in p.read_text(encoding="utf-8")]
    assert not missing, f"prompts that cannot render environment-state: {missing}"


# --------------------------------------------------------------------------- #
# Every environment's get_state has to accept the call its only caller makes
# --------------------------------------------------------------------------- #
def test_every_environment_accepts_the_get_state_call_the_manager_makes():
    """`EnvironmentContextManager.get_state` always passes `ctx=`. Nothing enforced it.

    The base class declared `async def get_state(self)` — no `ctx`, no `**kwargs` — so
    it disagreed with its only caller, and all six built-ins agreed with the caller
    instead. Nothing noticed, because the caller wraps the call and logs a warning
    rather than raising:

        ⚠️ could not read grid_maze_environment state:
           GridMazeEnvironment.get_state() got an unexpected keyword argument 'ctx'

    once per step, forever. Which is how an *evolved* environment — one that wrote
    `def get_state(self)`, matching the base class exactly — shipped with a state the
    agent never saw. The framework produced a component that obeyed the contract and
    could not run, and the contract was the thing that was wrong.

    Signature-checked rather than called: calling would start browsers and containers.
    """
    import inspect

    import agentevolver.environment.default  # noqa: F401 — importing is what registers them
    from agentevolver.registry import ENVIRONMENT

    wrong = []
    for name, cls in sorted(ENVIRONMENT.module_dict.items()):
        method = getattr(cls, "get_state", None)
        if method is None:
            continue
        if not inspect.iscoroutinefunction(method):
            wrong.append(f"{name}.get_state is not async; the manager awaits it")
            continue
        parameters = inspect.signature(method).parameters
        takes_ctx = "ctx" in parameters or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        if not takes_ctx:
            wrong.append(f"{name}.get_state{inspect.signature(method)} cannot take `ctx=`")

    assert not wrong, ("these environments reject the call the manager makes:\n  "
                       + "\n  ".join(wrong))
