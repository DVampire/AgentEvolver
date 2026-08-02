"""Binding an agent to one ECP environment.

Projecting an environment's actions into callable tools is the same work whatever the
environment is — read the registered actions, expose each as ``env__<name>``, route the
call back. It lives here rather than in an actor because it is not specific to any of
them: the browser and a remote host differ in what their actions *do*, not in how the
actions reach the model.

It is a mixin rather than a method on ``Agent`` because ``assemble_native_tools`` decides
whether to project env tools by asking ``hasattr(agent, "_native_env_tools")``. On the
base class that test would answer yes for every agent in the system, handing env tools to
agents that were never bound to an environment. Inheriting the mixin is the declaration.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from agentevolver.environment.server import environment_manager
from agentevolver.logger import logger


def render_action_result(result: Any) -> Any:
    """Turn one environment action's return value into something the model can read.

    Shared because an action reaches the run loop by two different routes — the
    ``env__*`` projection a bound agent makes, and the namespace-qualified projection
    ``environment_manager.function_callings()`` adds for every agent. Both hand their
    result to the same message, trace and memory chain, and that chain takes text. A dict
    travelling down it does not come out the other side: the loop goes silent, no error,
    no next step. Fixing only one route left the other one hanging.

    Raises:
        RuntimeError: If the environment reported failure, so the error reaches
            ``action_errors`` and is shown to the model on the next step.
    """
    if not isinstance(result, dict):
        return result
    if not result.get("success", False):
        raise RuntimeError(result.get("message") or "env action failed")
    # `message` when the action wrote one, otherwise everything else it returned. Reading
    # only `message` silently discarded the result of any action reporting structured data
    # instead — the agent got `None` back from a directory listing, a file read, a job
    # list, and kept re-running the same actions because nothing it did appeared to
    # produce anything.
    if "message" in result:
        return result["message"]
    payload = {k: v for k, v in result.items() if k != "success"}
    if not payload:
        return "(no output)"
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(payload)


class EnvironmentBound:
    """Mixin for agents that act on a single environment named by ``self.env_name``."""

    env_name: str = ""

    async def _env_info(self):
        try:
            return await environment_manager.get_info(self.env_name)
        except Exception:  # noqa: BLE001 — a missing env degrades to no actions
            return None

    async def _native_env_tools(
        self, ctx: Optional[Any] = None
    ) -> List[Tuple[str, Dict[str, Any], str, Tuple[str, str]]]:
        """Project this env's actions into native tools for the run loop.

        Returns ``[(ns, params, desc, route), ...]`` consumed by ``assemble_native_tools``:
        each action becomes an ``env__<name>`` tool routed back through
        ``_handle_env_action``.
        """
        out: List[Tuple[str, Dict[str, Any], str, Tuple[str, str]]] = []
        env_info = await self._env_info()
        if not env_info or not getattr(env_info, "actions", None):
            return out
        for action in env_info.actions.values():
            fc = getattr(action, "function_calling", None) or {}
            params = (fc.get("function", {}) or {}).get("parameters") or {
                "type": "object",
                "additionalProperties": True,
            }
            desc = getattr(action, "description", "") or action.name
            out.append((f"env__{action.name}", params, desc, ("env", action.name)))
        return out

    async def _handle_env_action(
        self,
        action_name: str,
        action_args: Dict[str, Any],
        ctx: Any,
    ) -> Any:
        """Dispatch one ``env__*`` tool call back to the environment action.

        ``ctx`` flows through so the environment resolves the per-session resource — the
        browser's tab, the remote host's connection (env ``session_id`` == ``ctx.id``).

        Raises:
            RuntimeError: If the environment reports the action failed, so the error
                reaches ``action_errors`` and is shown to the model on the next step.
        """
        result = await environment_manager(
            name=self.env_name, action=action_name, input=action_args, ctx=ctx
        )
        return render_action_result(result)

    async def _get_environment_context(self) -> Dict[str, Any]:
        """Render the environment's actions into the prompt's environment context."""
        env_info = await self._env_info()
        if not env_info or not env_info.actions:
            return {
                "environment_context": "### Available Environment Actions\n"
                                       "[No environment actions loaded.]"
            }
        parts = [f"### Available Environment Actions ({self.env_name})"]
        for action in env_info.actions.values():
            parts.append(action.text or f"- {action.name}: {action.description}")
        return {"environment_context": "\n\n".join(parts)}

    async def _observe(self, ctx: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        """Fetch the environment's current state for this session; None when unavailable."""
        try:
            return await environment_manager.get_state(self.env_name, ctx=ctx)
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ [{getattr(self, 'name', '?')}] env state unavailable: {e}")
            return None
