'''A tool to perform basic arithmetic operations (+, -, *, /) on two numbers.'''
from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL


@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    '''A tool to perform basic arithmetic operations (+, -, *, /) on two numbers.'''

    name: str = 'calculator_tool'
    description: str = 'Performs basic arithmetic operations (+, -, *, /, %, **) on two numbers. Args: a (float), b (float), op (str: +, -, *, /, %, **).'
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> Response:
        '''Execute the tool and return a Response.'''
        try:
            if op == '+':
                result = a + b
            elif op == '-':
                result = a - b
            elif op == '*':
                result = a * b
            elif op == '/':
                if b == 0:
                    raise ValueError('Division by zero error')
                result = a / b
            elif op == '%':
                if b == 0:
                    raise ValueError('Modulo by zero error')
                result = a % b
            elif op == '**':
                result = a ** b
            else:
                raise ValueError(f'Unknown operation: {op}')

            return Response(
                type=ResponseType.TOOL,
                success=True,
                message=str(result),
                data={'result': result}
            )
        except Exception as e:
            return Response(type=ResponseType.TOOL, success=False, message=str(e))
