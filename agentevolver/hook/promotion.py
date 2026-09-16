"""Getting what a run produced installed: dispatching the registration hook, and the
promotion bridge that hook uses once it has accepted a staged component."""

from pathlib import Path
from typing import Any, Optional, Tuple

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


async def install_generated_component(
    *,
    module: str,
    name: Optional[str],
    artifact_path: str,
    model_name: str = "",
    ctx: Any = None,
) -> Tuple[bool, str]:
    """Install what a run produced, through the hook that installs everything.

    A tool is a file, a skill is a directory, a workflow is compiled before it counts, an
    agent may be a prompt alone. One hook holds all eight shapes and is handed the type
    the run recorded — rather than this function, or the hook layer, growing a branch per
    shape.

    This used to take a finished worker's ``Response`` and be called from that worker's
    ``finalize``, because installing was something only the three evolution agents did.
    They are gone: the agent that decides to evolve now writes the artifact itself, and
    asks for it to be installed at the point it is ready rather than by ending a run. So
    the arguments are the facts the hook needs, and the caller is whoever has them.

    Args:
        module: Which component family was built. Must be evolvable, and is read from
            what the caller recorded rather than guessed from the artifact: a generated
            component does not exist yet, so nothing can be looked up.
        name: The component's name, used to locate the artifact and to report failure.
        artifact_path: Absolute path to what was written. Stated rather than mined out of
            the run's prose, which is what the hook did when the path arrived as part of a
            finished run's reasoning and there was nowhere else to put it.
        model_name: For the one shape that instantiates what it registers. Empty means the
            run's configured model.
        ctx: The calling run's context, for the hook manager.

    Returns:
        ``(True, message)`` once the component is registered, or ``(False, reason)`` with
        the hook's own reason — which says what to fix — when it refused.
    """
    from agentevolver.extension import EVOLVABLE_MODULES
    from agentevolver.hook.server import hook_manager
    from agentevolver.hook.types import HookDecision, HookEvent

    target = str(module or "")
    if target not in EVOLVABLE_MODULES:
        return False, (
            f"module must be one of {', '.join(sorted(EVOLVABLE_MODULES))}; got {target!r}. "
            f"Without it there is no way to know what was built or how to install it."
        )

    result = await hook_manager(
        name="registration_hook",
        input={
            "event": HookEvent.ON_STOP,
            "target_type": target,
            "target_name": name,
            "artifact_path": artifact_path,
            "reasoning": "",
            "model_name": model_name,
        },
        ctx=ctx,
        required=True,
    )
    if result.decision == HookDecision.BLOCK:
        return False, result.reason or (
            f"Registration failed; give the generated {target}'s absolute path as "
            f"`artifact_path`."
        )
    return True, f"Registered {target}:{name}. Live on the next dispatch."
