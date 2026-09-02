"""ReviewerAgent — the outer-loop critic: did the task actually get done?"""

from typing import Any, Dict

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class ReviewerAgent(Agent):
    """An independent critic dispatched to review a run.

    Not a per-entity grader — that is the evaluate agent. This one asks whether the user's
    task was accomplished, what defects remain, and whether the loop should continue. Its
    verdict is advisory; the orchestrator reads it and decides.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="reviewer_agent")
    description: str = Field(
        default="An independent critic that reviews, at the task/loop level, whether the user "
        "task was actually accomplished, what defects remain, whether self-evolution "
        "helped, and whether the loop should continue, evolve more, or stop — verifying "
        "the real deliverable hands-on, not just reading claims."
    )
    metadata: Dict[str, Any] = Field(default={})
    prompt_name: str = Field(default="reviewer_agent")
    max_step: int = Field(default=20)
    enable_evolving: bool = Field(default=False)


__all__ = ["ReviewerAgent"]
