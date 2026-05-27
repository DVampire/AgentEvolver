'''Calculator tool for basic arithmetic operations.'''
from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL

@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    '''A tool that performs basic arithmetic operations: add, subtract, multiply, divide.'''

    name: str = 'calculator_tool'
    description: str = (
        'Calculates the result of a basic arithmetic operation.\n'
        'Args:\n'
        '- a (float): The first operand.\n'
        '- b (float): The second operand.\n'
        '- op (str): The operation to perform. Must be one of +, -, *, /.\n'
    )
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> ToolResponse:
        '''Executes the calculation.'''
        if op == '+':
            result = a + b
        elif op == '-':
            result = a - b
        elif op == '*':
            result = a * b
        elif op == '/':
            if b == 0:
                raise ValueError('Division by zero is not allowed.')
            result = a / b
        else:
            raise ValueError(f'Unsupported operation: {op}')

        return ToolResponse(
            success=True,
            message=f'result: {result}',
            extra=ToolExtra(data={'result': result}),
        )
