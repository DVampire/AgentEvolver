"""Basic calculator tool for addition, subtraction, multiplication, and division."""
from typing import Any, Dict, Optional
from pydantic import Field
from src.tool.types import Tool, ToolResponse, ToolExtra
from src.registry import TOOL


@TOOL.register_module(force=True)
class CalculatorTool(Tool):
    """A tool that performs basic arithmetic operations."""

    name: str = "calculator_tool"
    description: str = (
        "Performs basic arithmetic operations (+, -, *, /) on two numbers.\n"
        "Args:\n"
        "- a (float): The first number.\n"
        "- b (float): The second number.\n"
        "- op (str): The operator, one of '+', '-', '*', '/'.\n"
    )
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=True)

    def __init__(self, require_grad: bool = True, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, a: float, b: float, op: str, **kwargs) -> ToolResponse:
        """Executes the arithmetic operation."""
        if op == '+':
            result = a + b
        elif op == '-':
            result = a - b
        elif op == '*':
            result = a * b
        elif op == '/':
            if b == 0:
                raise ValueError("Division by zero is not allowed.")
            result = a / b
        else:
            return ToolResponse(
                success=False,
                message=f"Unsupported operator: {op}",
                extra=ToolExtra(data={})
            )

        return ToolResponse(
            success=True,
            message=str(result),
            extra=ToolExtra(data={"result": result})
        )