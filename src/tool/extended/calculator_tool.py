'''A calculator tool that supports basic arithmetic operations.'''
from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL

@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    '''A calculator tool that supports basic arithmetic operations.'''

    name: str = 'calculator_tool'
    description: str = '''A calculator tool that supports basic arithmetic operations.
Args:
- a (float): The first number.
- b (float): The second number.
- op (str): The operation to perform (+, -, *, /).
'''
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> ToolResponse:
        '''Perform the calculation.'''
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
                    extra=ToolExtra(data={'error': 'division by zero'}),
                )
            result = a / b
        else:
            return ToolResponse(
                success=False,
                message=f'Error: Unsupported operation {op}',
                extra=ToolExtra(data={'error': 'unsupported operation'}),
            )
        
        return ToolResponse(
            success=True,
            message=f'Result: {result}',
            extra=ToolExtra(data={'result': result}),
        )
