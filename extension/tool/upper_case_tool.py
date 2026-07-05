from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = "Converts a string to uppercase using Python's str.upper()."

_INSTRUCTION = """
## Function
Converts the given text to uppercase using Python's built-in str.upper() and returns the uppercased string.

## Guidance
Use when you need an all-uppercase version of a string. An empty string returns an empty string. If the input is missing or not a string, the tool returns success=False with a clear error message instead of raising.

## Parameters
- text (str): The string to convert to uppercase.

## Example
{"name": "upper_case_tool", "args": {"text": "hello world"}}
"""


@TOOL.register_module(force=True)
class UpperCaseTool(Tool):
    """Converts a string to uppercase."""
    name: str = "upper_case_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, text: str = None, **kwargs) -> Response:
        """Convert the input text to uppercase."""
        if text is None:
            return Response(type=ResponseType.TOOL, success=False,
                            message="Missing required argument 'text'. Provide a string to convert to uppercase.")
        if not isinstance(text, str):
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"Invalid argument 'text': expected a string but got {type(text).__name__}.")
        result = text.upper()
        return Response(type=ResponseType.TOOL, success=True,
                        message=result,
                        data={"text": text, "upper": result})
