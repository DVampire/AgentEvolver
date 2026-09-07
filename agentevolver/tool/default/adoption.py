"""Adoption tool — does an evolved component stay?

Every action here serves that one question. `list_active` says what has been adopted,
`list_versions` what could be reverted to, `diff` what a change actually changed;
`record_workflow_evaluation` and `record_decision` are the evidence and the verdict;
`rollback` and `unload` un-adopt. The generate and optimize agents *create* versions —
this is the lever that decides whether one is kept, and the undo that makes a regression
caught by `reviewer_agent` actionable rather than only re-optimizable.

It was called `evolution_tool`, which named the topic rather than the operation — the
same way `database_tool` would tell a reader nothing about what it does. Every other
tool here is named for what it does: `bash_tool`, `deploy_tool`, `inspect_tool`.

Only affects evolved components under `extension/`; built-in capabilities are never
touched. Rollback restores an archived version over the active file and re-registers it
— a legitimate restore rather than an evolution-overwrite, so it is not subject to the
enable_evolving gate.

Granting a capability is NOT here. It answers a different question — may this process
use that component — and lived here only because this tool happened to be mounted where
the grant was needed. It is `grant_tool`.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field, ValidationError

from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Manage and record the evaluated lifecycle of evolved extension components."

_GUIDANCE = """
Manage the version lifecycle of evolved components (tools/agents/prompts/skills/environments/connectors/workflows created or optimized under `extension/`). Use it to UNDO a bad evolution a reviewer flagged — roll back to the previous good version, or unload a newly generated component that made things worse.

### Actions (pass `action`)
- `list_active`: list all active evolved components (module, name, version). No args.
- `list_versions`: list archived versions of one component. Args: `module`, `name`.
- `diff`: show the source diff between two versions (see what an optimization actually changed). Args: `module`, `name`, `version_a`, `version_b` (optional; defaults to the live version).
- `rollback`: restore a component to a previous version (becomes live immediately). Args: `module`, `name`, `version`.
- `unload`: unregister an evolved component (its archive is kept). Args: `module`, `name`.
- `record_workflow_evaluation`: append one version-scoped Workflow evaluation. Successful evidence requires a real terminal `run_id`; static failures require `case_id`. Args: `name`, `version`, `success`, `quality_score`, plus optional `run_id`, `case_id`, `token_cost`, `elapsed_ms`, `notes`.
- `record_decision`: record the outcome of an evolution you chose to start. Args: `report`
  (the version-scoped evaluation you performed — `module`, `name`, `version`, `verdict`,
  `baseline`, `cases`), `decision` (`keep|rollback|unload`), and `evidence` explaining the
  observed need and outcome. Optional `module`, `name`, `version` must match the report;
  `run_id` defaults to your own process. The version must already be archived, and keep
  additionally requires a passing verdict for the exact active version; perform
  rollback/unload separately. You are recording a judgment on your own work, so ground the
  verdict in cases you actually executed. This is generic across all eight families and does
  not depend on a website release or task contract.

`module` is one of: tool | agent | skill | environment | connector | workflow | plugin | memory.
The associated prompt can also be inspected/restored as an agent's supporting artifact.

- Pair with `reviewer_agent`: if the reviewer's verdict is that an evolution regressed the outcome, `rollback` to the prior version (use `list_versions` first to see what to roll back to), or `unload` a brand-new component that has no prior good version.
- Only affects `extension/` components; built-in capabilities cannot be rolled back/unloaded here.
- A rollback/unload takes effect for the NEXT dispatched sub-agent, not one already running.
"""

_EXAMPLES = [
    '{"name": "adoption_tool", "args": {"action": "rollback", "module": "tool", "name": "calculator_tool", "version": "1.0.0"}}',
]


def _require_observed_evidence(report: Dict[str, Any], caller_id: str) -> None:
    """Every cited evidence id must name a call this run actually made.

    An evaluation is only worth citing if its cases point at real calls. The evaluator that
    used to enforce this was a separate process whose retained turns were the record; the
    caller's own conversation is that record now.
    """
    cited = {
        str(evidence)
        for case in (report.get("cases") or [])
        if isinstance(case, dict)
        for evidence in (case.get("evidence_ids") or [])
    }
    if not cited:
        return
    from agentevolver.message.types import ToolMessage
    from agentevolver.runtime import kernel

    caller = kernel.get(caller_id) if caller_id else None
    conversation = getattr(getattr(caller, "agent", None), "conversation", None)
    observed = {
        str(message.tool_call_id)
        for message in (getattr(conversation, "items", ()) or ())
        if isinstance(message, ToolMessage) and getattr(message, "tool_call_id", None)
    }
    if not observed:
        return  # Nothing retained to check against; the version check below still applies.
    invented = sorted(cited - observed)
    if invented:
        raise ValueError(
            f"Evaluation cites calls absent from this run's evidence: {invented}. "
            "Every evidence_id must be a tool call you actually made."
        )


def _require_unchanged_candidate(report: Dict[str, Any]) -> None:
    """The evaluated version must still be the registered one."""
    from agentevolver.extension import extension_manager

    module, name = str(report.get("module") or ""), str(report.get("name") or "")
    version = str(report.get("version") or "")
    if not (module and name and version):
        return  # Shape errors are reported field-by-field by the validator below.
    current = extension_manager.read_manifest().find(module, name)
    if current is None or current.version != version:
        found = current.version if current is not None else "nothing registered"
        raise ValueError(
            f"Candidate version changed during evaluation: the report is for {version} but "
            f"{module}:{name} is now {found}. Evaluate the active version again."
        )


@TOOL.register_module(force=True)
class AdoptionTool(Tool):
    """Decide whether an evolved component stays: evidence, verdict, and undo."""

    name: str = "adoption_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(
        default=False, description="Whether the tool may be evolved (self-optimized)"
    )
    permission_mode: str = Field(
        default="workspace_write",
        description="Mutates the active set of evolved components under extension/.",
    )

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    def will_mutate(self, arguments: Dict[str, Any]) -> bool:
        # Recording validated workflow evidence is observational bookkeeping, like
        # Trace, not activation or mutation of a candidate. The workflow manager
        # independently validates its run provenance. Lifecycle decisions still write.
        return arguments.get("action", "list_active") not in {
            "list_active", "list_versions", "diff", "record_workflow_evaluation",
        }

    async def __call__(
        self,
        action: Literal[
            "list_active",
            "list_versions",
            "diff",
            "rollback",
            "unload",
            "record_workflow_evaluation",
            "record_decision",
        ] = "list_active",
        module: Optional[str] = None,
        name: Optional[str] = None,
        version: Optional[str] = None,
        version_a: Optional[str] = None,
        version_b: Optional[str] = None,
        success: Optional[bool] = None,
        quality_score: Optional[float] = None,
        run_id: Optional[str] = None,
        case_id: Optional[str] = None,
        token_cost: int = 0,
        elapsed_ms: float = 0.0,
        notes: str = "",
        decision: Optional[Literal["keep", "rollback", "unload"]] = None,
        evidence: str = "",
        report: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Response:
        """Inspect and roll back evolved components.

        Args:
            action: Which operation to run — ``list_active``, ``list_versions``,
                ``diff``, ``rollback``, ``unload``, ``record_workflow_evaluation``,
                or ``record_decision``. Defaults to ``list_active``.
            module: Component family for version, diff, rollback, unload, or decision actions.
            name: Registered component or workflow name for the selected action.
            version: Component/workflow version for rollback, evaluation, or decision evidence.
            version_a: Archived base version for ``diff``.
            version_b: Comparison version for ``diff``; the live version when omitted.
            success: Whether a workflow evaluation succeeded.
            quality_score: Workflow evaluation score.
            run_id: Optional decision provenance (defaults to your own process), or a terminal workflow run ID.
            case_id: Static evaluation case identifier, primarily for failed evidence.
            token_cost: Tokens consumed by a workflow evaluation.
            elapsed_ms: Workflow evaluation duration in milliseconds.
            notes: Optional workflow evaluation notes.
            decision: Evaluated candidate outcome: keep, rollback, or unload.
            evidence: Grounded reason for the task-scoped evolution decision.
            report: The version-scoped evaluation behind a ``record_decision``: ``module``,
                ``name``, ``version`` (exactly the one you evaluated), ``verdict``
                (pass|fail|inconclusive), ``baseline``, and ``cases`` — each with a unique
                ``case_id``, ``expected``, ``observed``, ``passed`` and non-empty
                ``evidence_ids`` naming calls you actually made.
            **kwargs: Runtime-only injected values, including the current context.
        """
        from agentevolver.extension import (
            extension_manager,  # local import avoids a heavy import at load
        )

        action = (action or "list_active").lower().strip()
        try:
            if action == "list_active":
                comps = extension_manager.read_manifest().components
                if not comps:
                    return Response(
                        type=ResponseType.TOOL,
                        success=True,
                        message="No evolved components active.",
                    )
                body = "\n".join(
                    ["module\tname\tversion\tfile"]
                    + [f"{c.module}\t{c.name}\t{c.version}\t{c.file}" for c in comps]
                )
                return Response(
                    type=ResponseType.TOOL,
                    success=True,
                    message=body,
                    data={"components": [c.model_dump() for c in comps]},
                )

            if action == "list_versions":
                if not module or not name:
                    raise KeyError("module and name")
                vers = extension_manager.list_component_versions(module, name)
                if not vers:
                    return Response(
                        type=ResponseType.TOOL,
                        success=False,
                        message=f"No archived versions for {module}:{name}.",
                    )
                return Response(
                    type=ResponseType.TOOL,
                    success=True,
                    message=f"{module}:{name} versions: {', '.join(vers)}",
                    data={"module": module, "name": name, "versions": vers},
                )

            if action == "diff":
                if not module or not name or not version_a:
                    raise KeyError("module, name, and version_a")
                diff = extension_manager.diff_versions(module, name, version_a, version_b)
                return Response(
                    type=ResponseType.TOOL,
                    success=True,
                    message=diff,
                    data={
                        "module": module,
                        "name": name,
                        "version_a": version_a,
                        "version_b": version_b,
                    },
                )

            if action == "rollback":
                if not module or not name or not version:
                    raise KeyError("module, name, and version")
                restored = await extension_manager.rollback(module, name, version)
                logger.info(f"| ⏪ adoption_tool: rolled back {module}:{restored} to v{version}")
                return Response(
                    type=ResponseType.TOOL,
                    success=True,
                    message=f"Rolled back {module}:{restored} to v{version} (live on next dispatch).",
                    data={"module": module, "name": restored, "version": version},
                )

            if action == "unload":
                if not module or not name:
                    raise KeyError("module and name")
                ok = await extension_manager.unload(module, name)
                return Response(
                    type=ResponseType.TOOL,
                    success=bool(ok),
                    message=(
                        f"Unloaded {module}:{name}." if ok else f"{module}:{name} was not active."
                    ),
                    data={"module": module, "name": name, "unloaded": bool(ok)},
                )

            if action == "record_workflow_evaluation":
                from agentevolver.workflow import WorkflowEvaluation, workflow_manager

                if not name or not version or success is None or quality_score is None:
                    raise KeyError("name, version, success, and quality_score")
                raw_success = success
                success = (
                    raw_success
                    if isinstance(raw_success, bool)
                    else str(raw_success).strip().lower() in {"true", "1", "yes"}
                )
                evaluation = WorkflowEvaluation(
                    workflow_name=name,
                    workflow_version=version,
                    run_id=run_id,
                    case_id=case_id,
                    success=success,
                    quality_score=float(quality_score),
                    token_cost=int(token_cost),
                    elapsed_ms=float(elapsed_ms),
                    notes=notes,
                )
                workflow_manager.record_evaluation(evaluation)
                summary = workflow_manager.evaluation_summary(evaluation.workflow_name)
                return Response(
                    type=ResponseType.TOOL,
                    success=True,
                    message=f"Recorded evaluation for workflow:{evaluation.workflow_name}. Summary: {summary}",
                    data={"evaluation": evaluation.model_dump(), "summary": summary},
                )

            if action == "record_decision":
                ctx = kwargs.get("ctx")
                caller_id = str((getattr(ctx, "extra", None) or {}).get("process_pid") or "")
                # The judgment is submitted rather than read out of a separate evaluator
                # process. What binds it is not who produced it but that it is version-scoped
                # and checked against the archive: `record_decision` below refuses a version
                # that was never archived, and refuses to keep anything but the exact active
                # version with a passing verdict. An evaluation naming no version, or one that
                # does not exist, establishes nothing and is rejected rather than stored.
                # The missing report is the more useful complaint: a caller that passed
                # prose, or nothing, needs to be told what shape is expected before being
                # told anything about process identity.
                if not report:
                    raise RuntimeError(
                        "Decision requires the version-scoped evaluation you performed: pass "
                        "`report` with module, name, version, verdict, baseline and cases"
                    )
                if not caller_id:
                    raise RuntimeError("Decision requires an identified calling process")
                # Two checks the separate read-only evaluator used to own. Without them a
                # report is only a claim: evidence ids could name calls that never
                # happened, and a candidate could be replaced between evaluating it and
                # keeping it, so the verdict would describe a version nobody installed.
                _require_observed_evidence(report, caller_id)
                _require_unchanged_candidate(report)
                for key, requested in (("module", module), ("name", name), ("version", version)):
                    if requested is not None and requested != report.get(key):
                        raise ValueError(f"Decision {key} differs from evaluated candidate")
                try:
                    record = extension_manager.record_decision(
                        report=report, run_id=run_id or caller_id, decision=decision,
                        evidence=evidence,
                    )
                except ValidationError as invalid:
                    # A pydantic dump names the model and links its docs; neither tells the
                    # caller which field to fix. Say the field and the rule instead, or the
                    # next attempt is a guess at the same shape.
                    problems = "; ".join(
                        f"{'.'.join(str(part) for part in error['loc']) or 'report'}: {error['msg']}"
                        for error in invalid.errors()
                    )
                    raise ValueError(
                        f"The evaluation report is not usable — {problems}. It needs module, "
                        "name, version (exactly the one you evaluated), verdict, baseline, and "
                        "cases with unique case_id and non-empty evidence_ids; a 'pass' verdict "
                        "requires at least one case and every case passing."
                    ) from None
                return Response(
                    type=ResponseType.TOOL, success=True,
                    message=f"Recorded {decision}: {report['module']}:{report['name']} v{report['version']}",
                    data={"decision": record},
                )
            return Response(
                type=ResponseType.TOOL, success=False, message=f"Unknown action {action!r}."
            )
        except KeyError as e:
            return Response(
                type=ResponseType.TOOL, success=False, message=f"Missing required arg: {e}"
            )
        except Exception as e:
            logger.error(f"| ❌ adoption_tool {action} failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Error: {e}")
