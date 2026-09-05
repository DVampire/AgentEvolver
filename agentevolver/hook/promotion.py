"""Getting what a run produced installed: dispatching the registration hook, and the
promotion bridge that hook uses once it has accepted a staged component."""

from pathlib import Path
from typing import Any

from agentevolver.sandbox.project import ProjectSandbox
from agentevolver.utils import get_extension_root


def promote_approved_component(extension_root: str, component_path: str) -> str:
    """Validate and promote exactly one approved staged extension component.

    The session staging root is never used as a durable extension directory.
    """
    staged_root = Path(extension_root).expanduser().resolve()
    component = Path(component_path).expanduser().resolve()
    try:
        relative = component.relative_to(staged_root)
    except ValueError as exc:
        raise ValueError(f"Component is outside staged extension root: {component}") from exc
    sandbox = ProjectSandbox.create(
        staged_root.parent,
        shared_extension_root=get_extension_root(),
    )
    if sandbox.extension_root != staged_root:
        raise ValueError(f"Invalid staged extension root: {extension_root}")
    report = sandbox.promote(overwrite=True, relative_paths=[str(relative)])
    if len(report["promoted"]) != 1:
        raise ValueError("Expected exactly one promoted extension component")
    return report["promoted"][0]["destination"]


async def register_generated(response: Any, ctx: Any, model_name: str, *, verb: str) -> Any:
    """Install what an evolution run produced, through the hook that installs everything.

    A tool is a file, a skill is a directory, a workflow is compiled before it counts, an
    agent may be a prompt alone. One hook holds all eight shapes and is handed the type
    the run recorded — rather than this function, or the hook layer, growing a branch per
    shape.

    Args:
        response: The finished run's response. A failed one is returned untouched: there
            is nothing to install, and the failure it already carries is the useful one.
        ctx: The run's context, carrying ``target_type`` and ``target_name``.
        model_name: Passed through for the types that instantiate what they register.
        verb: What to call the step in the failure message.

    Returns:
        The response, marked failed with the hook's reason if registration was blocked.
    """
    from agentevolver.extension import EVOLVABLE_MODULES
    from agentevolver.hook.server import hook_manager
    from agentevolver.hook.types import HookDecision, HookEvent

    if not response.success:
        return response
    extra = getattr(ctx, "extra", None) or {}
    target = str(extra.get("target_type") or "")
    if target not in EVOLVABLE_MODULES:
        # Read from the context rather than guessed from the task text: a generate run's
        # target does not exist yet, so nothing can be looked up, and a wrong guess picks
        # the wrong hook — which surfaces as "could not locate the generated file" rather
        # than as the type being wrong.
        response.success = False
        response.message = (
            f"target_type must be one of {', '.join(EVOLVABLE_MODULES)}; got {target!r}. "
            f"Without it there is no way to know what was built or how to install it."
        )
        return response

    result = await hook_manager(
        name="registration_hook",
        input={
            "event": HookEvent.ON_STOP,
            "target_type": target,
            "target_name": extra.get("target_name"),
            "artifact_path": (response.data or {}).get("artifact_path"),
            "reasoning": (response.data or {}).get("reasoning") or "",
            "model_name": model_name,
        },
        ctx=ctx,
        required=True,
    )
    if result.decision == HookDecision.BLOCK:
        response.success = False
        response.message = result.reason or (
            f"{verb} failed; include the generated {target}'s path in the done_tool reasoning."
        )
    return response
