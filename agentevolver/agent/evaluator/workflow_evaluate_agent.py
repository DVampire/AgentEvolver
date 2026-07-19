"""Evaluate one live Workflow version; it never edits artifacts."""

from typing import Any, Dict, Optional
from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class WorkflowEvaluateAgent(Agent):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = Field(default="workflow_evaluate_agent")
    description: str = Field(default="Evaluates a Workflow for safety, quality, reliability, and efficiency.")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = False

    def __init__(self, base_dir: str, prompt_name: Optional[str] = None, **kwargs):
        super().__init__(base_dir=base_dir, prompt_name=prompt_name or "workflow_evaluate_agent", **kwargs)
