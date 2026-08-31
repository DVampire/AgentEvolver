"""Hook failures follow the declared enforcement policy.

Telemetry hooks must not stop a run merely because logging failed, while an exception in
a permission or budget gate must never turn that gate off.  The policy is explicit on the
hook so new enforcement hooks cannot depend on a global all-open or all-closed choice.
"""

import pytest

from agentevolver.hook.context import HookContextManager
from agentevolver.hook.types import Hook, HookDecision, HookEvent, HookResult


class _BrokenHook(Hook):
    name: str = "broken"

    async def handle(self, _ctx):
        raise RuntimeError("boom")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_closed", "decision"),
    [(False, HookDecision.ALLOW), (True, HookDecision.BLOCK)],
)
async def test_hook_exception_respects_its_failure_policy(fail_closed, decision):
    manager = HookContextManager()
    await manager.register(
        _BrokenHook,
        config={"name": "broken", "fail_closed": fail_closed},
    )

    result = await manager(
        "broken", {"event": HookEvent.PRE_ACTION},
    )

    assert result.decision is decision
    if fail_closed:
        assert "boom" in (result.reason or "")


@pytest.mark.asyncio
async def test_lifecycle_broadcast_calls_only_explicit_subscribers():
    """Legacy named hooks must not become wildcards when the event bus is enabled.

    Registration and compaction hooks historically have an empty ``events`` list and
    are invoked directly by name. Broadcasting a pre-tool event to them can block an
    unrelated call, so lifecycle delivery is explicit opt-in.
    """
    seen = []

    class LegacyNamedHook(Hook):
        name: str = "legacy_named"

        async def handle(self, _ctx):
            seen.append("legacy")
            return HookResult.allow()

    class ToolSubscriber(Hook):
        name: str = "tool_subscriber"
        events: list = [HookEvent.PRE_TOOL_USE]

        async def handle(self, _ctx):
            seen.append("subscriber")
            return HookResult.allow()

    manager = HookContextManager()
    await manager.register(LegacyNamedHook)
    await manager.register(ToolSubscriber)

    result = await manager.emit(HookEvent.PRE_TOOL_USE, {"arguments": {}})

    assert result.decision is HookDecision.ALLOW
    assert seen == ["subscriber"]
