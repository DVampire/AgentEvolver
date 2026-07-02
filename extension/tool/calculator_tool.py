from typing import Any, Dict
from src.tool.base import BaseTool

class CalculatorTool(BaseTool):
    def __init__(self) -> None:
        super().__init__()
        self.tool_info = {
            "name": "calculator_tool",
            "description": "Performs basic arithmetic operations on two numbers.",
            "args": [
                {
                    "name": "a",
                    "type": "float",
                    "description": "Left operand."
                },
                {
                    "name": "b",
                    "type": "float",
                    "description": "Right operand."
                },
                {
                    "name": "op",
                    "type": "str",
                    "description": "One of +, -, *, /."
                }
            ]
        }

    def __call__(self, a: float, b: float, op: str) -> Dict[str, Any]:
        if op == '+':
            res = float(a + b)
        elif op == '-':
            res = float(a - b)
        elif op == '*':
            res = float(a * b)
        elif op == '/':
            if b == 0:
                raise ValueError("Division by zero")
            res = float(a / b)
        else:
            raise ValueError(f"Unknown operation: {op}")
        
        return {
            "message": str(res),
            "data": res
        }
