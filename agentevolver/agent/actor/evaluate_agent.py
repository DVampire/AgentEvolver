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
    prompt_name: str = Field(default="evaluate_agent")
    max_step: int = Field(default=20)
    enable_evolving: bool = Field(default=False)
    permission_mode: str = Field(default="read_only")

    async def prepare_task(self, task, files, ctx):
        from agentevolver.extension import extension_manager

        task, files = await super().prepare_task(task, files, ctx)
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            target = extension_manager.read_manifest().find(
                extra.get("target_type", ""), extra.get("target_name", ""),
            )
            extra["evaluation_version"] = target.version if target is not None else ""
        return task, files

    async def finalize(self, response):
        """Bind structured findings to this evaluator's target and observed calls."""
        import json
        from agentevolver.extension import extension_manager
        from agentevolver.extension.types import ComponentEvaluation
        from agentevolver.message.types import ToolMessage

        response.data = dict(response.data or {})
        response.data.pop("evaluation", None)
        response.data.pop("evaluation_error", None)
        if not response.success:
            return response
        extra = getattr(self.ctx, "extra", None) or {}
        if not extra.get("evaluation_version"):
            return response  # Built-in reviews need not be extension-adoption reports.
        try:
            text = response.message.strip()
            if text.startswith("```json\n") and text.endswith("```"):
                text = text[len("```json\n"):-3].strip()
            report = ComponentEvaluation.model_validate(json.loads(text))
            if (report.module, report.name, report.version) != (
                extra.get("target_type"), extra.get("target_name"), extra["evaluation_version"],
            ):
                raise ValueError("Evaluation identity differs from the bound candidate")
            observed = {message.tool_call_id for message in self.conversation.items
                        if isinstance(message, ToolMessage)}
            if any(not set(case.evidence_ids).issubset(observed) for case in report.cases):
                raise ValueError("Evaluation cites calls absent from its retained evidence")
            current = extension_manager.read_manifest().find(report.module, report.name)
            if current is None or current.version != report.version:
                raise ValueError("Candidate version changed during evaluation; evaluate the active version again")
            response.data = {**(response.data or {}), "evaluation": report.model_dump()}
        except (ValueError, TypeError) as error:
            response.data = {**(response.data or {}), "evaluation_error": str(error)}
        return response

    def allow_read_only(self, name: str, args: Dict[str, Any]) -> bool:
        """The one mutating call a read-only evaluator needs: recording its own verdict.

        Permission is per action rather than per tool, so being allowed to call
        ``adoption_tool`` does not let this run reach ``rollback`` through it.
        """
        return name == "adoption_tool" and args.get("action") == "record_workflow_evaluation"

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
