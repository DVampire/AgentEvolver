"""MonitorAgent — watches one long-running command and reports what it is doing.

An ordinary tool-calling agent. It used to be four hundred lines of subprocess plumbing
— spawn, drain stdout, tail it, poll on a timer, terminate on a budget, post progress —
and every one of those is something the framework already does:

    bash_tool(command, run_in_background=True)   start it, get a job id back
    job__output(job_id, ...)                     what it has printed so far
    job__wait(job_id, ...)                       block until it exits
    job__kill(job_id)                            stop it

So this agent declares those and nothing else. That is the point the rewrite was for:
an agent is a declaration plus the loop, the difference between agents is a prompt and a
roster, and what differs *beyond* that belongs to the runtime — which mode drives it —
rather than to a bespoke ``__call__``.

Reporting to a parent is likewise the runtime's: a dispatched child's progress goes
through ``report_tool``, and the kernel posts its final report when it exits.
"""

from typing import Any, Dict, List

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class MonitorAgent(Agent):
    """Starts a long-running command, watches it, and reports progress and outcome."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="monitor_agent")
    description: str = Field(
        default="Starts a long-running bash process and monitors it, reporting progress "
        "to the parent agent at intervals and the exit code when it finishes."
    )
    metadata: Dict[str, Any] = Field(default={})
    prompt_name: str = Field(default="monitor_agent")
    #: Watching is cheap and mostly waiting, so the budget is turns, not thinking.
    max_step: int = Field(default=60)
    max_actions: int = Field(default=1)
    enable_evolving: bool = Field(default=False)
    #: `job` is what makes a background command observable: without it the agent gets a
    #: job id it can never read or kill.
    env_names: List[str] = Field(default=["job"])
    capability_allowlists: Dict[str, List[str]] = Field(default={
        "tool": ["bash_tool", "report_tool", "done_tool"],
        "skill": [],
        "connector": [],
        "plugin": [],
        "workflow": [],
    })

    #: Seconds the prompt asks it to leave between progress reports.
    poll_interval: int = Field(default=30)
    #: Seconds after which the prompt asks it to kill the process and report a timeout.
    max_wait: int = Field(default=3600)
    #: Output lines to include in each progress report.
    tail_lines: int = Field(default=50)

    async def prompt_modules(self, ctx: Any) -> Dict[str, Any]:
        """The watching policy, so the numbers live in config rather than in prose."""
        modules = await super().prompt_modules(ctx)
        modules.update(
            poll_interval=self.poll_interval,
            max_wait=self.max_wait,
            tail_lines=self.tail_lines,
        )
        return modules


__all__ = ["MonitorAgent"]
