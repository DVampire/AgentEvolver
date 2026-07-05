from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = "Counts the number of whitespace-separated words in an input string."

_INSTRUCTION = """
## Function
Counts the number of words in the given text. Words are whitespace-separated tokens, using Python's str.split() semantics.

## Guidance
Use this to get a quick word count of any text. An empty string (or a string containing only whitespace) returns 0. Leading, trailing, and repeated whitespace are handled correctly and do not inflate the count.

## Parameters
- text (str): The input string whose words are counted.

## Example
{"name": "word_count_tool", "args": {"text": "  hello   world  "}}
"""


@TOOL.register_module(force=True)
class WordCountTool(Tool):
    """Counts the number of whitespace-separated words in an input string."""
    name: str = "word_count_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, text: str, **kwargs) -> Response:
        """Count whitespace-separated words in ``text``.

        Args:
            text (str): The input string whose words are counted.

        Returns:
            Response: ``data['count']`` holds the word count as an integer.
            An empty or whitespace-only string yields 0.
        """
        if not isinstance(text, str):
            return Response(type=ResponseType.TOOL, success=False,
                            message="'text' must be a string.", data={})
        count = len(text.split())
        return Response(type=ResponseType.TOOL, success=True,
                        message=str(count), data={"count": count})
