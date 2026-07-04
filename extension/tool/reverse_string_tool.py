from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = "Reverses a string character-by-character and returns the reversed string."

_INSTRUCTION = """
## Function
Reverses an input string character-by-character and returns the reversed string. An empty string returns an empty string.

## Guidance
Use this to reverse the order of characters in a string. It is unicode-aware and operates per code point via Python slicing. It does not perform grapheme-cluster-aware reversal, so strings containing combining marks or emoji with modifiers may not reverse as a human would expect. Do NOT use it for reversing words or lines.

## Parameters
- text (str): The input string to reverse. An empty string returns an empty string.

## Example
{"name": "reverse_string_tool", "args": {"text": "hello"}}
"""


@TOOL.register_module(force=True)
class ReverseStringTool(Tool):
    """Reverse an input string character-by-character."""
    name: str = "reverse_string_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, text: str = "", **kwargs) -> Response:
        """Reverse the given string and return the result."""
        if text is None:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message="'text' must be a string, got None.",
                data={},
            )
        if not isinstance(text, str):
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=f"'text' must be a string, got {type(text).__name__}.",
                data={},
            )
        reversed_text = text[::-1]
        return Response(
            type=ResponseType.TOOL,
            success=True,
            message=reversed_text,
            data={"text": text, "reversed": reversed_text},
        )
