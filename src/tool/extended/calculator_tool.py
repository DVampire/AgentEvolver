'''A simple calculator tool supporting basic arithmetic operations.'''
from typing import Any, Dict, Optional
from pydantic import Field
from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL

@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    '''A simple calculator tool supporting basic arithmetic operations.'''

    name: str = 'calculator_tool'
    description: str = (
        'A simple calculator tool supporting basic arithmetic operations.\n'
        'Args:\n'
        '- a (float): The first operand.\n'
        '- b (float): The second operand.\n'
        '- op (str): The operator, one of +, -, *, /.\n'
    )
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> ToolResponse:
        '''Execute the calculation.'''
        if op == '+':
            result = a + b
        elif op == '-':
            result = a - b
        elif op == '*':
            result = a * b
        elif op == '/':
            if b == 0:
                return ToolResponse(
                    success=False,
                    message='Error: Division by zero is not allowed.',
                    extra=ToolExtra(data={})
                )
            result = a / b
        else:
            return ToolResponse(
                success=False,
                message=f'Error: Unsupported operator {op}',
                extra=ToolExtra(data={})
            )

        return ToolResponse(
            success=True,
            message=f'result: {result}',
            extra=ToolExtra(data={'result': result}),
        )
