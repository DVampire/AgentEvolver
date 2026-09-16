"""A tool declares what one call of it is allowed to cost.

Every tool used to share one 1800-second budget, which has to be generous enough
for the slowest legitimate call — a build. The consequence is paid by the fastest:
a `read_file_tool` against a wedged NFS mount held the agent for thirty minutes
before it learned anything, and the message it eventually got named a timeout that
told it nothing about which of its tools was the slow one.

The budget belongs to the tool because the tool is what knows the cost of its work.
Reading it from the registry rather than from the call site also means no caller can
name a tool that does not exist.
"""

import asyncio
from types import SimpleNamespace

from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.context import ToolContextManager
from agentevolver.tool.types import Tool


class _Slow(Tool):
    """Sleeps past any budget a test gives it."""

    name: str = "slow_tool"
    description: str = "Never finishes in time."
    call_timeout_seconds: float = 0.05

    async def __call__(self, ctx=None, **kwargs) -> Response:
        await asyncio.sleep(30)
        return Response(type=ResponseType.TOOL, success=True, message="finished")


class _Quiet(Tool):
    """Declares nothing, and so inherits the manager default."""

    name: str = "quiet_tool"
    description: str = "Returns at once."

    async def __call__(self, ctx=None, **kwargs) -> Response:
        return Response(type=ResponseType.TOOL, success=True, message="ok")


class _Nonsense(Tool):
    """Declares a budget that cannot be honoured."""

    name: str = "nonsense_tool"
    description: str = "Declares a bad budget."
    call_timeout_seconds: float = -1

    async def __call__(self, ctx=None, **kwargs) -> Response:
        return Response(type=ResponseType.TOOL, success=True, message="ok")


def _manager_for(tmp_path, instance, default_timeout=1800.0):
    manager = ToolContextManager(base_dir=str(tmp_path), default_timeout=default_timeout)

    async def _fake_get_info(name):
        return SimpleNamespace(version="1.0.0", instance=instance)

    manager.get_info = _fake_get_info
    return manager


def test_a_declared_budget_is_enforced(tmp_path):
    manager = _manager_for(tmp_path, _Slow())
    resp = asyncio.run(manager(name="slow_tool", input={}, ctx=SimpleNamespace(id="c", extra={})))

    assert resp.success is False
    assert "slow_tool" in resp.message  # names the tool, not just "a tool"
    assert "0.05" in resp.message  # and the budget it blew
    # And says what to do about it, which "timed out" alone does not.
    assert "narrower" in resp.message or "split" in resp.message


def test_the_declared_budget_wins_over_the_manager_default(tmp_path):
    """0.05s must be what fires — a 30s default would make this test hang instead."""
    manager = _manager_for(tmp_path, _Slow(), default_timeout=30.0)

    async def timed():
        loop = asyncio.get_running_loop()
        start = loop.time()
        resp = await manager(name="slow_tool", input={}, ctx=SimpleNamespace(id="c", extra={}))
        return resp, loop.time() - start

    resp, elapsed = asyncio.run(timed())
    assert resp.success is False
    assert elapsed < 5, f"waited {elapsed:.1f}s, so the tool's own budget was ignored"


def test_a_tool_that_declares_nothing_keeps_the_default(tmp_path):
    manager = _manager_for(tmp_path, _Quiet())
    assert manager._call_timeout(_Quiet()) == 1800.0
    resp = asyncio.run(manager(name="quiet_tool", input={}, ctx=SimpleNamespace(id="c", extra={})))
    assert resp.success is True


def test_an_unusable_declared_budget_falls_back_rather_than_failing_every_call(tmp_path):
    """A typo in an evolved tool should not read as the tool being broken.

    Honouring ``-1`` would fail every call instantly, and the agent would conclude the
    capability does not work. Ignoring it with a warning keeps the tool usable and puts
    the fault where an operator will see it.
    """
    manager = _manager_for(tmp_path, _Nonsense(), default_timeout=5.0)
    assert manager._call_timeout(_Nonsense()) == 5.0


def test_bash_leaves_room_for_its_own_diagnostic():
    """The command budget must fire before the call budget.

    If the pipeline cut the call off first, the agent would get "tool timed out" and
    lose what bash_tool reports: the command, the elapsed limit, and partial output.
    """
    from agentevolver.tool.default.workspace.bash import BashTool

    tool = BashTool()
    assert tool.call_timeout_seconds > tool.timeout
