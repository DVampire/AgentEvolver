'''Calculator tool for basic arithmetic operations.'''
from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL


@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    '''Performs basic arithmetic operations.'''

    name: str = 'calculator_tool'
    description: str = 'Performs basic arithmetic operations. Args: a (float), b (float), op (str: +, -, *, /).'
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> ToolResponse:
        '''Executes the calculator tool.'''
        if op == '+':
            result = a + b
        elif op == '-':
            result = a - b
        elif op == '*':
            result = a * b
        elif op == '/':
            if b == 0.0:
                return ToolResponse(
                    success=False,
                    message='Error: Division by zero is not allowed.',
                    extra=ToolExtra(data={})
                )
            result = a / b
        else:
            return ToolResponse(
                success=False,
                message=f'Error: Unsupported operation {op}',
                extra=ToolExtra(data={})
            )

        return ToolResponse(
            success=True,
            message=f'Result of {a} {op} {b} is {result}',
            extra=ToolExtra(data={'result': result})
        )
