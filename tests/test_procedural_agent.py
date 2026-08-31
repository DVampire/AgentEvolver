"""A procedural agent is an agent: same entry point, same lifecycle, same hook record.

`ProceduralAgent` replaces the LLM loop with code, which makes it tempting to treat as a
plain callable and let it bypass the mailbox. It must not be. The memory, trace, and
trajectory hooks fire from `on_start`, and everything downstream — what a run left in
memory, what the trace surface shows, what evolution later trains on — exists only if
those hooks ran. An agent that records its work when the runtime called it but not when
code called it directly produces a history whose gaps depend on the call site, which is
the hardest kind of missing data to notice.

The type name is the other half. `AgentType.PROCEDURAL` used to be spelled `"workflow"`,
and configs written then are still on disk; an unmapped value raises out of the Enum at
registration, so the agent simply would not appear.
"""

import asyncio
from typing import Any
from unittest.mock import patch

from pydantic import Field

from agentevolver.agent.types import (
    AgentConfig,
    AgentContext,
    AgentType,
    ProceduralAgent,
)
from agentevolver.response import Response, ResponseType
from agentevolver.runtime import runtime_manager


class EchoProcedure(ProceduralAgent):
    name: str = Field(default="echo_procedure")
    description: str = Field(default="Deterministic test procedure")
    metadata: dict[str, Any] = Field(default_factory=dict)

    async def run_procedure(self, task, files, ctx, **kwargs):
        # The reply encodes all three things a caller can pass — task, context, extra
        # kwargs — so one string shows whether each survived the route it took.
        return Response(
            type=ResponseType.AGENT,
            success=True,
            message=f"{task}:{ctx.workspace_root}:{kwargs.get('suffix', '')}",
        )


class FakeHooks:
    """Stands in for the hook manager, recording only (hook, event, agent_type)."""

    def __init__(self):
        self.events = []

    async def __call__(self, name, input, ctx):
        self.events.append((name, input["event"], input["agent_type"]))


def test_a_config_written_as_workflow_still_loads_as_procedural() -> None:
    """The old informal spelling is mapped, not rejected.

    `AgentType` is a str Enum, so an unknown value raises rather than degrading. Any
    config file still saying `"workflow"` would then fail at registration and take its
    agent out of the roster — visible as a capability that quietly stopped existing, not
    as an error anyone connects to a rename.
    """
    assert AgentType("workflow") is AgentType.PROCEDURAL


def test_an_agent_type_survives_a_dump_and_reload() -> None:
    """Registered configs are persisted and re-validated, so the round trip is the real path.

    An `agent_type` that dumped to something `model_validate` could not read back would
    fall to the field default, `TOOL_CALLING` — meaning a procedural agent reloaded as one
    that expects an LLM loop, and the mismatch shows up only when it is next run.
    """
    config = AgentConfig(
        name="procedure",
        description="test",
        agent_type=AgentType.PROCEDURAL,
    )
    restored = AgentConfig.model_validate(config.model_dump())
    assert restored.agent_type is AgentType.PROCEDURAL


def test_a_direct_call_and_a_delegated_one_leave_the_same_lifecycle_record(tmp_path) -> None:
    """Calling the agent yourself and going through the runtime must be indistinguishable.

    `__call__` is the only public entry point precisely so both routes land in the same
    `on_start`. If a direct call skipped it, half the procedural runs in a session would
    leave no memory, trace, or trajectory behind, and the surviving half would look like
    the complete record.

    The event count is the load-bearing assertion: it is the only way to catch a route
    that ran the procedure and produced a correct-looking `Response` while emitting
    nothing. `agent_type` is checked on every event because the hooks route on it — an
    event labelled `tool_calling` would be filed against the wrong contract downstream.
    """

    async def check() -> None:
        hooks = FakeHooks()
        agent = EchoProcedure(base_dir=str(tmp_path))
        ctx = AgentContext(id="direct", workspace_root=str(tmp_path))
        with patch("agentevolver.hook.server.hook_manager", hooks):
            direct = await agent(task="direct", ctx=ctx, suffix="ok")
            delegated = await runtime_manager.invoke(agent, task="delegated", ctx=ctx, suffix="ok")
        assert direct.success and direct.message.endswith(":ok")
        assert delegated.success and delegated.message.startswith("delegated:")
        # Memory consumes the exact seq-numbered event inside trace_hook; it is no
        # longer called independently with a second look-alike event.
        assert len(hooks.events) == 8  # start/stop × trace+trajectory × two runs
        assert all(event[2] == "procedural" for event in hooks.events)
        # The delegated run spawned a ref; nothing may still hold it afterwards.
        assert runtime_manager.list() == []

    asyncio.run(check())
