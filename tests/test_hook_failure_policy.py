"""Hook failures follow the declared enforcement policy.

Telemetry hooks must not stop a run merely because logging failed, while an exception in
a permission or budget gate must never turn that gate off.  The policy is explicit on the
hook so new enforcement hooks cannot depend on a global all-open or all-closed choice.
"""

import pytest

from agentevolver.hook.context import HookContextManager
from agentevolver.hook.types import Hook, HookDecision, HookEvent


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
