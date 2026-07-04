"""Inspect-agent tool — fetch a registered agent's live registry facts on demand."""
import os
from typing import Dict, Any
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL
from src.utils import get_project_root

_DESCRIPTION = "Fetch a registered agent's live registry facts (registration/instantiation status, version, evolvability/require_grad, file paths) by name."

_INSTRUCTION = """
## Function
Fetch an agent's live registry facts: whether it is registered and instantiated in the current process, its version and description, whether it is evolvable (require_grad), its agent type, and its Python class / HTML prompt file paths.

## Guidance
- Use this before optimizing or evaluating an agent named in your task: it reports the target's real state in the live registry, which reading files alone cannot.
- Optimization requires require_grad=True. If inspect_agent reports require_grad=False, the agent is frozen — do NOT optimize it; refuse and report why.
- The returned file paths tell you exactly which files to read/edit.

## Parameters
- name (str): The exact name of the agent to inspect.

## Example
{"name": "inspect_agent", "args": {"name": "my_generated_agent"}}
"""


@TOOL.register_module(force=True)
class InspectAgent(Tool):
    """Return another registered agent's live registry facts on demand."""

    name: str = "inspect_agent"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, name: str, **kwargs) -> Response:
        """Return live registry facts for the named agent.

        Args:
            name (str): The exact name of the agent to inspect.
        """
        from src.agent.server import agent_manager  # local import avoids a circular import

        info = await agent_manager.get_info(name)
        instance = await agent_manager.get(name) if info is not None else None

        project_root = get_project_root()
        py_path = os.path.join(project_root, "extension", "agent", f"{name}.py")
        html_path = os.path.join(project_root, "extension", "prompt", f"{name}.html")
        html_exists = os.path.exists(html_path)

        lines = [f"- **Agent Name**: `{name}`"]
        if info is None:
            lines.append("- **Registered**: False (not found in registry)")
        else:
            lines.append("- **Registered**: True")
            lines.append(f"- **Description**: {info.description}")
            lines.append(f"- **Version**: {info.version}")
            lines.append(f"- **Evolvable (require_grad)**: {getattr(info, 'require_grad', False)}")
        lines.append(f"- **Instantiated (live instance available)**: {instance is not None}")
        lines.append(f"- **Python File**: `{py_path}` (exists: {os.path.exists(py_path)})")
        lines.append(f"- **HTML Prompt File**: `{html_path}` (exists: {html_exists})")
        lines.append(f"- **Agent Type**: {'tool_calling' if html_exists else 'workflow'}")

        if info is None:
            available = await agent_manager.list()
            lines.append(f"\nAvailable agents: {available}")

        return Response(
            type=ResponseType.TOOL,
            success=info is not None,
            message="\n".join(lines),
            data={
                "agent": name,
                "registered": info is not None,
                "require_grad": bool(getattr(info, "require_grad", False)) if info else False,
                "instantiated": instance is not None,
            },
        )
