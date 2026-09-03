"""grant tool — widen one running sub-agent's roster by one named capability.

A roster bound by `capability_allowlists` states an isolation contract: a website
visitor holds one tool and eleven browser actions, and must keep meeting the product
through the page rather than reading its source. That contract is written in the actor's
own file, so a component evolved during a run cannot enter it by itself — which made the
one participant that genuinely lacked a capability also the one that could never be
given it.

There are three ways to widen a roster and this is the middle one. A process that does
not exist yet gets its scope with the dispatch that creates it. A process that declared
`accepts_evolved` takes new components of that type as they register, needing no
addressing at all. This is for the case neither covers: a process already running, whose
roster was written before the thing it now needs existed, and which the caller can name.

It is deliberately per-component and per-process. Widening a whole type for a whole run
is what `accepts_evolved` is for, declared in the file that states the contract where a
reader will find it; a grant is a specific, auditable act with a named subject.
"""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = (
    "Give one running sub-agent access to one capability its own roster excludes."
)

_GUIDANCE = """
Widen a live sub-agent's roster by one named capability. Use it when a component this run created or improved is needed by a sub-agent whose own allowlist does not include it, and that sub-agent is already running.

- `job_id` is the pid a dispatch returned, or the pid of a subscriber this run started.
- `module` is the capability family: tool | skill | connector | plugin | workflow.
- `name` is the exact registered name.
- It takes effect on that process's NEXT step. A turn already in progress is not interrupted.

When the sub-agent has not been dispatched yet, pass its scope in the dispatch arguments instead — `tool_allowlist`, `skill_allowlist`, and so on — which is the same grant made at the moment of creation.

A grant that names something not registered is accepted and has no effect: a roster is built from what exists. Check with `inspect_tool` if you are unsure the component registered.
"""

_EXAMPLES = [
    '{"name": "grant_tool", "args": {"job_id": "7d817bfd", "module": "tool", "name": "media_probe_tool"}}',
]

#: Families a roster can be scoped by. `agent` and `environment` are absent because
#: neither is gated by an allowlist: sub-agent visibility is one `include_agents` flag,
#: and an environment is mounted by `env_names` rather than filtered by name.
GRANTABLE: tuple = ("tool", "skill", "connector", "plugin", "workflow")


@TOOL.register_module(force=True)
class GrantTool(Tool):
    """Grant one named capability to one named live process."""

    name: str = "grant_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(
        default=False, description="Whether the tool may be evolved (self-optimized)"
    )
    #: It changes what another process is permitted to do, which is an effect on
    #: something outside this agent — the same reason a sub-agent dispatch declares one.
    mutates: bool = True

    def __init__(self, enable_evolving: bool = False, **kwargs: Any) -> None:
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(
        self,
        job_id: str,
        module: str,
        name: str,
        **kwargs: Any,
    ) -> Response:
        """Add one capability to a live sub-agent's allowlist.

        Args:
            job_id: The live sub-agent, by the pid a dispatch returned.
            module: Capability family — tool, skill, connector, plugin, or workflow.
            name: The exact registered name of the component to grant.
        """
        try:
            if module not in GRANTABLE:
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=(
                        f"{module!r} is not scoped by an allowlist. Grantable families: "
                        f"{', '.join(GRANTABLE)}."
                    ),
                )

            from agentevolver.agent.loop.router import grant as mark_granted
            from agentevolver.runtime import kernel

            target = kernel.get(str(job_id))
            if target is None or not target.alive:
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=(
                        f"No live process {job_id!r}. A grant is for a sub-agent that is "
                        "already running; pass the pid a dispatch returned."
                    ),
                )
            extra = getattr(target.ctx, "extra", None)
            if not isinstance(extra, dict):
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=f"Process {job_id!r} has no context to grant into.",
                )

            key = f"{module}_allowlist"
            scope = list(extra.get(key) or [])
            if name in scope:
                return Response(
                    type=ResponseType.TOOL, success=True,
                    message=(
                        f"{target.name} [{job_id}] already had {module}:{name}; its "
                        f"{key} is unchanged."
                    ),
                    data={"job_id": str(job_id), key: scope},
                )
            scope.append(name)
            extra[key] = scope
            # Marked as granted, or the next step re-derives this list from the agent's
            # class field — which is the roster this grant exists to widen.
            mark_granted(extra, key)
            logger.info(
                f"| 🎫 granted {module}:{name} to {target.name}:{str(job_id)[:8]}"
            )
            return Response(
                type=ResponseType.TOOL, success=True,
                message=(
                    f"Granted {module}:{name} to {target.name} [{job_id}]. Its {key} is "
                    f"now {scope}; it takes effect on that process's next step."
                ),
                data={"job_id": str(job_id), key: scope},
            )
        except Exception as error:  # noqa: BLE001 - a tool failure is an observation
            logger.error(f"| ❌ grant_tool failed: {error}")
            return Response(
                type=ResponseType.TOOL, success=False,
                message=f"Grant failed: {error}",
            )


__all__ = ["GRANTABLE", "GrantTool"]
