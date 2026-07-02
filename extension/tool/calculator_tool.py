'''Calculator tool that performs the four basic arithmetic operations.'''
from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL


@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    '''Performs basic arithmetic operations (+, -, *, /) on two numbers.'''

    name: str = 'calculator_tool'
    description: str = (
        'Performs basic arithmetic operations (+, -, *, /) on two numbers.\n'
        'Args:\n'
        '- a (float): Left operand.\n'
        '- b (float): Right operand.\n'
        '- op (str): One of +, -, *, /.\n'
    )
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> Response:
        '''Execute the arithmetic operation and return a Response.'''
        try:
            a = float(a)
            b = float(b)
            if op == '+':
                result = a + b
            elif op == '-':
                result = a - b
            elif op == '*':
                result = a * b
            elif op == '/':
                if b == 0:
                    raise ZeroDivisionError('Division by zero is not allowed: cannot divide {0} by 0.'.format(a))
                result = a / b
            else:
                raise ValueError('Unknown operation: {0!r}. Supported operations are: +, -, *, /.'.format(op))
            message = '{0} {1} {2} = {3}'.format(a, op, b, result)
            return Response(
                type=ResponseType.TOOL,
                success=True,
                message=message,
                data={'result': result},
            )
        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=str(e))
