"""The think-and-act loop: an agent as a process, and nothing else.

| Module          | Owns                                                        |
|-----------------|-------------------------------------------------------------|
| `agent.py`      | the declaration and the loop — `__call__`, `think`, `act`    |
| `decision.py`   | one model turn, and one action's result                      |
| `executor.py`   | running a turn's batch: parallel when safe, else serial      |
| `router.py`     | the loop's only view of the capability system                |
| `guards.py`     | step middleware — budget and no-progress                     |

What is deliberately absent: prompt assembly (that is
:mod:`agentevolver.agent.context`), scheduling, suspension and messaging (that is
:mod:`agentevolver.runtime`), and any per-capability special case (that is the router).
"""

from agentevolver.agent.loop.agent import Agent
from agentevolver.agent.loop.decision import ActionCall, ActionResult, Decision
from agentevolver.agent.loop.executor import ActionExecutor
from agentevolver.agent.loop.guards import LandingWindow, NoProgress, RepeatedActions
from agentevolver.agent.loop.router import CapabilityRouter, ToolRouter

__all__ = [
    "ActionCall",
    "ActionExecutor",
    "ActionResult",
    "Agent",
    "CapabilityRouter",
    "Decision",
    "LandingWindow",
    "NoProgress",
    "RepeatedActions",
    "ToolRouter",
]
