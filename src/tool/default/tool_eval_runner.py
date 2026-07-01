"""In-process tool invocation runner for evaluation.

Invokes a registered tool *inside the framework process*, using the live tool
registry, and returns a raw structured record of what happened. This avoids the
whole class of problems that arise when an evaluator shells out to a fresh
`python` subprocess and tries to re-import a generated tool by file path: no path
resolution, no importlib, no subprocess, no environment mismatch, and no ~40s
re-import of the entire `src` dependency tree on every call.

It is deliberately generic and makes NO assumptions about the tool's argument
names or output shape: it calls `tool(**input)` and reports the raw Response plus
timing / interface facts. The evaluator (LLM) decides what "expected" means and
judges correctness/robustness/interface from the returned record.
"""
import time
from typing import Any, Dict, Optional

from pydantic import Field

from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = """Invoke a registered tool in-process and return a raw structured record of the call.

Use this to exercise a tool during evaluation instead of writing a standalone script:
it runs against the live registry, so there is no import / path / subprocess overhead.
Call it once per test case; judge correctness/robustness/interface yourself from the record.

Args:
- name (str): the registered name of the tool to invoke (e.g. "calculator_tool").
- input (dict): the keyword arguments to pass to that tool, exactly as the tool expects
  (e.g. {"a": 2, "b": 3, "op": "+"}). Pass invalid/edge inputs here to test robustness.

Returns `data` with:
- tool_success (bool|None): the invoked tool's Response.success (None if it raised).
- tool_message (str|None), tool_data (any): the tool's Response.message / Response.data.
- raised (str|None): "ExcType: msg" if the tool raised an unhandled exception (a robustness failure), else null.
- interface_ok (bool): whether the return conformed to the Response contract (success:bool, message:str, data:dict|None).
- elapsed_ms (float): wall time of the single call (import excluded).

Example: {"name": "tool_eval_runner", "args": {"name": "calculator_tool", "input": {"a": 1, "b": 0, "op": "/"}}}
"""


@TOOL.register_module(force=True)
class ToolEvalRunnerTool(Tool):
    """Invokes a registered tool in-process and reports a raw structured call record."""

    name: str = "tool_eval_runner"
    description: str = _DESCRIPTION
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=False)

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, name: str, input: Optional[Dict[str, Any]] = None, **kwargs) -> Response:
        """Invoke tool ``name`` with ``input`` kwargs via the live registry, once."""
        from src.tool.server import tool_manager

        instance = await tool_manager.get(name)
        if instance is None:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=(
                    f"Tool '{name}' is not registered / has no live instance. "
                    "Confirm it was generated and registered before evaluating."
                ),
            )

        call_kwargs = input or {}
        if not isinstance(call_kwargs, dict):
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message="`input` must be a dict of keyword arguments for the target tool.",
            )

        record: Dict[str, Any] = {"name": name, "input": call_kwargs}
        try:
            t0 = time.perf_counter()
            r = await instance(**call_kwargs)
            record["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 4)
            record["raised"] = None
            record["tool_success"] = getattr(r, "success", None)
            record["tool_message"] = getattr(r, "message", None)
            record["tool_data"] = getattr(r, "data", None)
            record["interface_ok"] = (
                isinstance(getattr(r, "success", None), bool)
                and isinstance(getattr(r, "message", None), str)
                and (getattr(r, "data", None) is None or isinstance(r.data, dict))
            )
            summary = (
                f"tool_success={record['tool_success']} interface_ok={record['interface_ok']} "
                f"{record['elapsed_ms']}ms data={record['tool_data']}"
            )
        except Exception as e:
            record["elapsed_ms"] = None
            record["raised"] = f"{type(e).__name__}: {e}"
            record["tool_success"] = None
            record["tool_message"] = None
            record["tool_data"] = None
            record["interface_ok"] = False
            summary = f"RAISED {record['raised']}"

        # The runner itself succeeded in observing the call (even if the tool raised);
        # the tool's own outcome lives in `data`.
        return Response(
            type=ResponseType.TOOL,
            success=True,
            message=f"Invoked '{name}' with {call_kwargs}: {summary}",
            data=record,
        )
