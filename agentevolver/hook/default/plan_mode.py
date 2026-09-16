"""The plan-mode gate: while a run is held to planning, refuse anything with effects.

This is one of the few hooks here that actually blocks, and it earns that the way
``repeat_tool.py`` says a blocking hook has to. It does not interpret, infer or
classify: it asks the capability what the capability declared about itself, and a
capability that declared nothing is refused. The claim is small enough to be true
— "this thing did not say it is free of effects" — and the person who closed the
gate is the one who decided that claim should stop the run.

Stateless, like the other hooks here. The flag lives on ``plan_manager``, keyed by
the run, so two sessions in plan mode at once cannot see each other's gate.

Fires on ``PRE_ACTION``, per action rather than per batch, because that is the
firing whose ``BLOCK`` the agent honours — the batch-level ``PRE_ACTION`` exists to
advise and its decision is not read.
"""

from __future__ import annotations

from agentevolver.hook.types import Hook, HookContext, HookEvent, HookResult
from agentevolver.registry import HOOK


@HOOK.register_module(force=True)
class PlanModeHook(Hook):
    """Block effectful actions while a run is waiting for its plan to be approved."""

    name: str = "plan_mode_hook"
    description: str = "Refuses actions with declared or undeclared effects until a person approves the plan."
    events: list = []
    #: Ahead of the advisory hooks. A refused action should not also be counted
    #: toward a repetition chain it never got to run.
    priority: int = 2
    fail_closed: bool = True

    async def handle(self, ctx: HookContext) -> HookResult:
        from agentevolver.plan.server import (
            PLAN_MODE_NOTICE,
            action_is_allowed,
            declaration_of,
            plan_manager,
        )

        inp = ctx.input or {}
        if inp.get("event") != HookEvent.PRE_ACTION:
            return HookResult.allow()

        action = inp.get("action") or {}
        if not action:
            return HookResult.allow()

        session_id = ctx.id or ""
        if not plan_manager.active(session_id):
            return HookResult.allow()

        capability_type = str(action.get("type") or "tool")
        name = str(action.get("name") or "")
        declaration = await declaration_of(capability_type, name)
        if action_is_allowed(capability_type, name, declaration):
            return HookResult.allow()

        return HookResult.block(
            f"`{name}` was not run: it is not declared free of effects, and this run is "
            f"in plan mode. {PLAN_MODE_NOTICE}"
        )


__all__ = ["PlanModeHook"]
