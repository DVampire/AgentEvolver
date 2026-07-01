"""In-process environment evaluation runner.

Exercises a registered environment *inside the framework process*, using the live
environment registry. Like `tool_eval_runner`, this avoids shelling out to a fresh
subprocess that re-imports the whole `src` tree (~40s) with an empty registry.

Two modes:
- No `action`: calls the environment's `get_state()` and reports its shape + the
  list of registered actions (the Functionality baseline).
- With `action`: invokes that action in-process via the environment manager and
  reports the raw result (real functional test — environments are cheap to run,
  no model needed).

Makes no assumptions about action argument names or output shape.
"""
import time
from typing import Any, Dict, Optional

from pydantic import Field

from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = """Exercise a registered environment in-process and return a raw structured record.

Use this to test an environment during evaluation instead of writing a script — it runs
against the live registry (no import / path / subprocess overhead).

Args:
- name (str): the registered environment name (e.g. "artifact_renderer").
- action (str, optional): an action name to invoke. Omit to just probe `get_state()`.
- input (dict, optional): keyword arguments for the action (when `action` is given).

Returns `data` with:
- registered (bool), instance_name (str|None), actions (list of registered action names).
- get_state_ok (bool): whether get_state() returned a dict containing a "state" key.
- get_state / get_state_raised: the raw get_state() result, or the exception string.
- when `action` is given: action_result / action_raised — the invocation result or exception.

Example: {"name": "environment_eval_runner", "args": {"name": "artifact_renderer", "action": "render", "input": {"html": "<p>hi</p>"}}}
"""


@TOOL.register_module(force=True)
class EnvironmentEvalRunnerTool(Tool):
    """Exercises a registered environment in-process and reports a raw structured record."""

    name: str = "environment_eval_runner"
    description: str = _DESCRIPTION
    metadata: Dict[str, Any] = Field(default={})
    require_grad: bool = Field(default=False)

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, name: str, action: Optional[str] = None,
                       input: Optional[Dict[str, Any]] = None, **kwargs) -> Response:
        """Probe environment ``name`` in-process (get_state, and optionally one action)."""
        from src.environment.server import environment_manager

        instance = await environment_manager.get(name)
        if instance is None:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=(
                    f"Environment '{name}' is not registered / has no live instance. "
                    "Confirm it was generated and registered before evaluating."
                ),
            )

        record: Dict[str, Any] = {
            "registered": True,
            "instance_name": getattr(instance, "name", None),
            "actions": sorted((getattr(instance, "actions", None) or {}).keys()),
        }

        # --- get_state() probe ---
        try:
            state = await instance.get_state()
            record["get_state"] = state
            record["get_state_raised"] = None
            record["get_state_ok"] = isinstance(state, dict) and "state" in state
        except Exception as e:
            record["get_state"] = None
            record["get_state_raised"] = f"{type(e).__name__}: {e}"
            record["get_state_ok"] = False

        # --- optional action invocation ---
        if action:
            call_input = input or {}
            if not isinstance(call_input, dict):
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message="`input` must be a dict of keyword arguments for the action.",
                )
            try:
                t0 = time.perf_counter()
                result = await environment_manager(name=name, action=action, input=call_input, ctx=kwargs.get("ctx"))
                record["action"] = action
                record["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 4)
                record["action_result"] = getattr(result, "data", result)
                record["action_message"] = getattr(result, "message", None)
                record["action_raised"] = None
            except Exception as e:
                record["action"] = action
                record["action_result"] = None
                record["action_raised"] = f"{type(e).__name__}: {e}"

        summary = f"actions={record['actions']} get_state_ok={record['get_state_ok']}"
        if action:
            summary += f" action[{action}]_raised={record.get('action_raised')}"
        return Response(
            type=ResponseType.TOOL,
            success=True,
            message=f"Probed environment '{name}': {summary}",
            data=record,
        )
