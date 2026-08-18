"""EvaluateAgent — judges an existing component of whichever type it is given."""

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.extension import EVOLVABLE_MODULES
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class EvaluateAgent(Agent):
    """Evaluates one existing component's behaviour and returns a scored report.

    Produces no registrable artifact, so it has no ``_finalize_run`` — which is the whole
    reason evaluating is a separate role from optimizing rather than a flag on it. The
    target is named by ``target_name`` and typed by ``target_type``; the agent calls
    ``inspect_tool`` for its instruction, registry facts and source path.

    A grader with write access can resolve a bad grade by editing the thing it is
    grading, so this run is read-only with exactly one exception: recording its own
    verdict.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="evaluate_agent")
    description: str = Field(
        default="Evaluates an existing component — " + ", ".join(EVOLVABLE_MODULES) + " — "
                "and reports findings. Pass `target_type` to say which kind."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = Field(default=False)
    permission_mode: str = Field(default="read_only")

    def __init__(self, base_dir: str, prompt_name: Optional[str] = None,
                 max_step: int = 20, **kwargs: Any):
        super().__init__(base_dir=base_dir, prompt_name=prompt_name or "evaluate_agent",
                         max_step=max_step, **kwargs)

    def _allow_read_only_tool_call(self, name: str, input: Dict[str, Any]) -> bool:
        """The one mutating call a read-only evaluator needs: recording its own verdict.

        Permission is per action rather than per tool, so being allowed to call
        ``evolution_tool`` does not let this run reach ``rollback`` through it.
        """
        return name == "evolution_tool" and input.get("action") == "record_workflow_evaluation"

    def _target_capability_allowlists(self, target_name: Optional[str],
                                      target_type: Optional[str] = None) -> Dict[str, Any]:
        """Scope the run to the single component under evaluation.

        For a workflow this also opts the type in: workflows are projected only when a
        run names one, and naming it in the allowlist is that signal — so an evaluator
        can execute its target without also gaining arbitrary sub-agent delegation.
        """
        if target_name and target_type:
            return {f"{target_type}_allowlist": [target_name]}
        return {}
