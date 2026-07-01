from typing import Any, Dict, Optional
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    '''Performs basic arithmetic operations on two numbers.'''

    name: str = 'calculator_tool'
    description: str = (
        "Performs basic arithmetic operations on two numbers.\n"
        "Args:\n"
        "- a (float): Left operand.\n"
        "- b (float): Right operand.\n"
        "- op (str): One of +, -, *, /.\n"
    )
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=False)

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> Response:
        '''Execute the tool and return a Response.'''
        try:
            if op == '+':
                result = float(a + b)
            elif op == '-':
                result = float(a - b)
            elif op == '*':
                result = float(a * b)
            elif op == '/':
                if b == 0:
                    raise ValueError('Division by zero error')
                result = float(a / b)
            else:
                raise ValueError(f'Unknown operation: {op}')

            return Response(
                type=ResponseType.TOOL,
                success=True,
                message=str(result),
                data={"result": result}
            )
        except Exception as e:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=str(e),
                data=None
            )
