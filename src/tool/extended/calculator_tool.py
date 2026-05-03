"""Calculator tool for basic arithmetic operations."""
from typing import Any, Dict, Optional
from pydantic import Field
from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL


@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    """A tool that performs basic arithmetic operations: add, subtract, multiply, divide."""

    name: str = "calculator_tool"
    description: str = (
        "Performs basic arithmetic operations (+, -, *, /) on two numbers.\
"
        "Args:\
"
        "- a (float): The first number.\
"
        "- b (float): The second number.\
"
        "- op (str): The operator, one of '+', '-', '*', '/'.\
"
    )
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> ToolResponse:
        """Execute the arithmetic operation."""
        try:
            a = float(a)
            b = float(b)
        except ValueError:
            return ToolResponse(
                success=False,
                message="Error: 'a' and 'b' must be numbers.",
                extra=ToolExtra(data={"error": "InvalidInput"})
            )

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
                    message="Error: Division by zero is not allowed.",
                    extra=ToolExtra(data={"error": "DivisionByZero"})
                )
            result = a / b
        else:
            return ToolResponse(
                success=False,
                message=f"Error: Unsupported operator '{op}'. Use '+', '-', '*', or '/'.",
                extra=ToolExtra(data={"error": "UnsupportedOperator"})
            )

        return ToolResponse(
            success=True,
            message=f"The result of {a} {op} {b} is {result}.",
            extra=ToolExtra(data={"result": result, "a": a, "b": b, "op": op}),
        )