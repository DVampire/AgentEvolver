"""Evaluate one live Workflow version; it never edits artifacts."""

from typing import Any, Dict, Optional
from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent, AgentContext
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class WorkflowEvaluateAgent(Agent):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = Field(default="workflow_evaluate_agent")
    description: str = Field(default="Evaluates a Workflow for safety, quality, reliability, and efficiency.")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = False
    permission_mode: str = Field(default="read_only")

    def __init__(self, base_dir: str, prompt_name: Optional[str] = None, **kwargs):
        super().__init__(base_dir=base_dir, prompt_name=prompt_name or "workflow_evaluate_agent", **kwargs)

    def _include_workflows(self) -> bool:
        """Expose only Workflow callables, without granting sub-agent orchestration."""
        return True

    def _allow_read_only_tool_call(self, name: str, input: Dict[str, Any]) -> bool:
        return name == "evolution_tool" and input.get("action") == "record_workflow_evaluation"

    def _target_capability_allowlists(self, target_name: Optional[str]) -> Dict[str, Any]:
        return {"workflow_allowlist": [target_name]} if target_name else {}

    async def _get_workflow_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        from agentevolver.workflow import workflow_manager

        allowlist = (getattr(ctx, "extra", None) or {}).get("workflow_allowlist")
        content = workflow_manager.get_instruction(allowlist=allowlist)
        available = content or "[No workflows loaded.]"
        return {
            "workflow_context": f"### Available Workflows\n{available}",
            "available_workflows": available,
        }
