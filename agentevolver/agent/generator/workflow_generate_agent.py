"""Generate and register a reusable HTML Workflow."""

from typing import Any, Dict, Optional
from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class WorkflowGenerateAgent(Agent):
    """Thin authoring agent; workflow_creator_skill owns the methodology."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    name: str = Field(default="workflow_generate_agent")
    description: str = Field(default="Generates and registers a reusable, parameterized HTML Workflow.")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = False

    def __init__(self, base_dir: str, prompt_name: Optional[str] = None, **kwargs):
        super().__init__(base_dir=base_dir, prompt_name=prompt_name or "workflow_generate_agent", **kwargs)

    async def _finalize_run(self, response, ctx):
        return await _register_workflow(response, ctx, self.model_name)


async def _register_workflow(response, ctx, model_name):
    if not response.success:
        return response
    from agentevolver.hook.server import hook_manager
    from agentevolver.hook.types import HookDecision, HookEvent
    from agentevolver.sandbox.project import staged_extension_root
    result = await hook_manager(
        name="workflow_registration_hook",
        input={"event": HookEvent.ON_STOP,
               "target_name": (
                   (getattr(ctx, "input", None) or {}).get("target_name")
                   or (getattr(ctx, "extra", None) or {}).get("target_name")
               ),
               "artifact_path": (response.data or {}).get("artifact_path"),
               "reasoning": (response.data or {}).get("reasoning") or "",
               "extension_root": staged_extension_root(ctx), "model_name": model_name},
        ctx=ctx,
    )
    if result.decision == HookDecision.BLOCK:
        response.success = False
        response.message = result.reason or "Workflow registration failed."
    return response
