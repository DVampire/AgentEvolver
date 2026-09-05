"""MetaAgent — an ordinary agent with sub-agents in its roster.

There is no orchestration machinery here. An orchestrator is an agent that can call
other agents, so ``include_agents`` is the whole of it: the router projects every
registered agent as a callable, and dispatching one spawns a child process whose parent
is this one.

What it adds are guards, not machinery:

``NoProgress``        the guard every orchestrator wants and no leaf agent needs, since
                      an orchestrator can spend a whole budget dispatching and reading.
``RepeatedActions``   the other way a run stalls: the identical batch issued again. The
                      two see different shapes and neither sees the other's.
``CapabilityChanges`` evolution registers a component mid-run, and the model is told
                      rather than left to discover the new function next turn.

Everything else — decomposition, running children in parallel, collecting them, finishing
— is the ordinary loop. Independent children can run concurrently via background
dispatch; blocking dispatches are sequential. Children are collected because the kernel posts each child's final report to
its parent's mailbox; a blocked child is unblocked by calling ``reply_tool`` on the turn
its question arrives.

That last one used to be a side model call made from ``on_event``, off the loop and
without tools, bought to save one step of latency. It cost a full-context turn per
escalation and left no trace of the decision in the conversation — the parent could not
later see what it had told a child. A blocked child's question now lands in the live
layer like any other message and is answered in the ordinary turn.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import ConfigDict, Field

from agentevolver.agent.loop.guards import (
    CapabilityChanges,
    LandingWindow,
    NoProgress,
    RepeatedActions,
)
from agentevolver.agent.types import Agent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class MetaAgent(Agent):
    """Orchestrator: a normal agent whose roster includes other agents."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="meta_agent")
    description: str = Field(
        default="Orchestrator that decomposes tasks, dispatches sub-agents concurrently, "
        "reacts to their reports, and triggers self-evolution when a capability is "
        "missing or a sub-agent underperforms."
    )
    metadata: Dict[str, Any] = Field(default={})
    prompt_name: str = Field(default="meta_agent")
    max_step: int = Field(default=50)
    enable_evolving: bool = Field(default=False)
    #: The one line that makes this an orchestrator.
    include_agents: bool = Field(default=True)

    def __init__(self, base_dir: str = "", **kwargs: Any) -> None:
        super().__init__(base_dir=base_dir, **kwargs)
        if not self.middleware:
            self.middleware = [
                LandingWindow(), NoProgress(), RepeatedActions(),
                CapabilityChanges(),
            ]


__all__ = ["MetaAgent"]
