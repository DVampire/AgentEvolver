from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = "Title-cases a string using Python's str.title()."

_INSTRUCTION = """
## Function
Converts an input string to title case, where the first character of each word is capitalized and the remaining characters are lowercased, using Python's str.title().

## Guidance
Use this to normalize titles, headings, or names into title case. Note that str.title() treats any non-letter (including apostrophes) as a word boundary, so words like "don't" become "Don'T". Do NOT use it when strict grammatical title casing (e.g., leaving articles lowercase) is required.

## Parameters
- text (str): The input string to title-case.

## Example
{"name": "title_case_tool", "args": {"text": "hello world"}}
"""


@TOOL.register_module(force=True)
class TitleCaseTool(Tool):
    """Title-cases an input string."""
    name: str = "title_case_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, text: str, **kwargs) -> Response:
        """Return the title-cased version of the input string."""
        if not isinstance(text, str):
            return Response(type=ResponseType.TOOL, success=False,
                            message="'text' must be a string.")
        result = text.title()
        return Response(type=ResponseType.TOOL, success=True, message=result,
                        data={"text": text, "title_case": result})
