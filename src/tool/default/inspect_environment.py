"""Inspect-environment tool — fetch a registered environment's live registry facts on demand."""
import os
from typing import Dict, Any
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL
from src.utils import get_project_root

_DESCRIPTION = "Fetch a registered environment's live registry facts (registration status, version, evolvability/require_grad, source file / ENVIRONMENT.md path) by name."

_INSTRUCTION = """
## Function
Fetch an environment's live registry facts: whether it is registered, its version, whether it is evolvable (require_grad), and its source file / ENVIRONMENT.md paths.

## Guidance
- Use this before optimizing or evaluating an environment named in your task: it reports the target's real state in the registry, which reading files alone cannot.
- Optimization requires require_grad=True. If inspect_environment reports require_grad=False, the environment is frozen — do NOT optimize it; refuse and report why.
- The returned paths tell you exactly which files to read/edit.

## Parameters
- name (str): The exact name of the environment to inspect.

## Example
{"name": "inspect_environment", "args": {"name": "my_environment"}}
"""


@TOOL.register_module(force=True)
class InspectEnvironment(Tool):
    """Return a registered environment's live registry facts on demand."""

    name: str = "inspect_environment"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, name: str, **kwargs) -> Response:
        """Return live registry facts for the named environment.

        Args:
            name (str): The exact name of the environment to inspect.
        """
        from src.environment.server import environment_manager  # local import avoids a circular import

        info = await environment_manager.get_info(name)

        root = get_project_root()
        # Two possible layouts: a single-file module or a directory with environment.py + ENVIRONMENT.md.
        py_flat = os.path.join(root, "extension", "environment", f"{name}.py")
        env_dir = os.path.join(root, "extension", "environment", name)
        py_dir = os.path.join(env_dir, "environment.py")
        md_path = os.path.join(env_dir, "ENVIRONMENT.md")

        lines = [f"- **Environment Name**: `{name}`"]
        if info is None:
            lines.append("- **Registered**: False (not found in registry)")
            available = await environment_manager.list()
            lines.append(f"\nAvailable environments: {available}")
            return Response(
                type=ResponseType.TOOL, success=False, message="\n".join(lines),
                data={"environment": name, "registered": False, "require_grad": False},
            )

        lines.append("- **Registered**: True")
        lines.append(f"- **Description**: {getattr(info, 'description', '')}")
        lines.append(f"- **Version**: {getattr(info, 'version', '')}")
        lines.append(f"- **Evolvable (require_grad)**: {getattr(info, 'require_grad', False)}")
        lines.append(f"- **Python File (flat)**: `{py_flat}` (exists: {os.path.exists(py_flat)})")
        lines.append(f"- **Python File (dir)**: `{py_dir}` (exists: {os.path.exists(py_dir)})")
        lines.append(f"- **ENVIRONMENT.md**: `{md_path}` (exists: {os.path.exists(md_path)})")

        return Response(
            type=ResponseType.TOOL,
            success=True,
            message="\n".join(lines),
            data={
                "environment": name,
                "registered": True,
                "require_grad": bool(getattr(info, "require_grad", False)),
            },
        )
