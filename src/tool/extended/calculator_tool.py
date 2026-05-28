'''A calculator tool for basic arithmetic operations.'''
from typing import Any, Dict, Optional
from pydantic import Field
from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL

@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    '''A calculator tool that supports add, subtract, multiply, and divide operations.'''

    name: str = 'calculator_tool'
    description: str = (
        'A calculator tool that performs basic arithmetic operations. '
        'Args: a (float): The first operand. b (float): The second operand. '
        'op (str): The operation to perform. Supported: "+", "-", "*", "/".'
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
                raise ValueError('Division by zero is not allowed.')
            result = a / b
        else:
            raise ValueError(f'Unsupported operation {op}. Supported operations are "+", "-", "*", "/".')
        
        return ToolResponse(
            success=True,
            message=f'The result of {a} {op} {b} is {result}',
            extra=ToolExtra(data={'result': result}),
        )
