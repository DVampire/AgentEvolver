"""Inspect tool — fetch a registered tool's full instruction on demand."""
from typing import Dict, Any
from pydantic import Field
from src.tool.types import Tool
from src.tool.server import tool_manager
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = "Fetch the full instruction (function, guidance, parameters, examples) of a registered tool by name."

_INSTRUCTION = """
## Function
Fetch the full instruction (function, guidance, parameters, examples) of a registered tool by name.

## Guidance
- The tool context lists only each tool's name and one-line description. Before calling a tool whose arguments you are unsure of, call inspect_tool to read its full instruction.
- Pass the exact tool name as shown in the tool context.

## Parameters
- name (str): The exact name of the tool to inspect.

## Example
{"name": "inspect_tool", "args": {"name": "bash_tool"}}
"""


@TOOL.register_module(force=True)
class InspectTool(Tool):
    """A tool that returns another registered tool's full instruction on demand."""

    name: str = "inspect_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, name: str, **kwargs) -> Response:
        """Return the full instruction of the named tool.

        Args:
            name (str): The exact name of the tool to inspect.
        """
        info = await tool_manager.get_info(name)
        if info is None:
            available = await tool_manager.list()
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=f"Tool '{name}' not found. Available tools: {available}",
            )

        instruction = (getattr(info, "instruction", "") or "").strip()
        if not instruction:
            # No authored instruction — fall back to the short description.
            instruction = f"## Function\n{info.description}"

        return Response(
            type=ResponseType.TOOL,
            success=True,
            message=instruction,
            data={"tool": name},
        )
