from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = "Strips leading and trailing whitespace from an input string and returns the trimmed result."

_INSTRUCTION = """
## Function
Removes leading and trailing whitespace from the given text using Python's str.strip() and returns the trimmed string.

## Guidance
Use this tool to normalize a string by removing surrounding whitespace (spaces, tabs, newlines) before further processing or comparison. It only trims the outer edges; internal whitespace is preserved. Not suitable for collapsing internal whitespace.

## Parameters
- text (str): The input string to trim.

## Example
{"name": "trim_tool", "args": {"text": "  hello world  "}}
"""


@TOOL.register_module(force=True)
class TrimTool(Tool):
    """Strip leading and trailing whitespace from an input string."""

    name: str = "trim_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, text: str, **kwargs) -> Response:
        """Strip leading and trailing whitespace from text and return the result."""
        if not isinstance(text, str):
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=f"Expected 'text' to be a string, got {type(text).__name__}.",
            )
        trimmed = text.strip()
        return Response(
            type=ResponseType.TOOL,
            success=True,
            message=trimmed,
            data={"text": text, "trimmed": trimmed},
        )
