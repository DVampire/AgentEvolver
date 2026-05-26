'''Calculator tool for basic arithmetic.'''
from typing import Any, Dict, Optional
from pydantic import Field
from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL


@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    '''
    A tool that performs basic arithmetic operations: add, subtract, multiply, and divide.

    Args:
    - a (float): The first number.
    - b (float): The second number.
    - op (str): The operator ('+', '-', '*', '/').
    '''

    name: str = 'calculator_tool'
    description: str = (
        'Performs basic arithmetic operations (add, subtract, multiply, divide).\n'
        'Args:\n'
        '- a (float): First operand.\n'
        '- b (float): Second operand.\n'
        '- op (str): Operation to perform: \'+\', \'-\', \'*\', or \'/\'.\n'
    )
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> ToolResponse:
        '''Executes the arithmetic operation.'''
        try:
            if op == '+':
                result = a + b
            elif op == '-':
                result = a - b
            elif op == '*':
                result = a * b
            elif op == '/':
                if b == 0:
                    raise ValueError('Division by zero')
                result = a / b
            else:
                raise ValueError(f'Invalid operator: {op}')

            return ToolResponse(
                success=True,
                message=f'The result of {a} {op} {b} is {result}.',
                extra=ToolExtra(data={'result': result})
            )
        except Exception as e:
            return ToolResponse(
                success=False,
                message=str(e),
                extra=ToolExtra(data={'error': type(e).__name__})
            )