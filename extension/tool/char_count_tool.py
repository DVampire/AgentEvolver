"""char_count_tool — counts the number of characters in an input string.

Active file placed at extension/tool/char_count_tool.py. Registration is automatic
via the tool_registration_hook once the path is reported in done_tool reasoning.
"""

from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = "Counts the total number of characters in an input string."

_INSTRUCTION = """
## Function
Returns the total number of characters (the length) of the given input string,
including whitespace and punctuation.

## Guidance
Use when you need a deterministic character count of a piece of text. The count
equals `len(text)` — every character (letters, digits, spaces, newlines,
punctuation) is counted. If you need a word count instead, use word_count_tool.

## Parameters
- text (str): The input string to measure. Required.

## Example
{"name": "char_count_tool", "args": {"text": "hello world"}}
"""


@TOOL.register_module(force=True)
class CharCountTool(Tool):
    """Counts the number of characters in an input string."""

    name: str = "char_count_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=True, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, text: str = None, **kwargs) -> Response:
        """Count the characters in `text` and return the total as a Response."""
        try:
            if text is None:
                return Response(
                    type=ResponseType.TOOL,
                    success=False,
                    message="Missing required argument 'text' (a string to measure).",
                )
            if not isinstance(text, str):
                text = str(text)
            count = len(text)
            return Response(
                type=ResponseType.TOOL,
                success=True,
                message=f"The input string has {count} characters.",
                data={"text": text, "char_count": count},
            )
        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=str(e))
