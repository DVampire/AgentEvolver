'''One-line description of what this tool does.'''
from typing import Any, Dict, Optional
from pydantic import Field
from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL


@TOOL.register_module(force=True)
class MyTool(Tool):
    '''Docstring explaining what the tool does.'''

    name: str = 'my_tool'
    description: str = (
        'Human-readable description used by agents to decide when to call this tool.\n'
        'Args:\n'
        '- param1 (str): description of param1\n'
    )
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, param1: str, **kwargs) -> ToolResponse:
        '''Execute the tool and return a ToolResponse.'''
        try:
            # --- implementation ---
            result = f'processed: {param1}'
            return ToolResponse(
                success=True,
                message=result,
                extra=ToolExtra(data={'result': result}),
            )
        except Exception as e:
            return ToolResponse(success=False, message=str(e))
