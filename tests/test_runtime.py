"""Integration tests for src.runtime.

Covers the public surface of runtime_manager and the contextvar / parent_ref
plumbing that MetaAgent + EscalationHook rely on. Uses inline mock agents so
the tests don't need any real LLM / agent registry setup.
"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.runtime import (
    AgentDeadError,
    AgentStatus,
    BaseMessage,
    TaskMessage,
    current_ref,
    runtime_manager,
)


# --------------------------------------------------------------------------
# Mock agents
# --------------------------------------------------------------------------

class Echo:
    name = "echo"
    async def _run(self, task=None, **kwargs):
        return f"echo:{task}"


class Slow:
    name = "slow"
    def __init__(self, delay: float = 0.5):
        self.delay = delay
    async def _run(self, task=None, **kwargs):
        await asyncio.sleep(self.delay)
        return f"slow:{task}"


class Crashy:
    name = "crashy"
    async def _run(self, task=None, **kwargs):
        if task == "boom":
            raise ValueError("boom")
        return f"ok:{task}"


class Spawner:
    """Spawns a child and inspects parent linkage via current_ref()."""
    name = "spawner"
    async def _run(self, task=None, **kwargs):
        me = current_ref()
        assert me is not None, "current_ref() returned None inside pump"
        child = await runtime_manager.spawn(Echo(), parent_ref=me)
        try:
            result = await runtime_manager.ask(child, TaskMessage(task="hi"))
        finally:
            await runtime_manager.stop(child)
        # Return everything the test wants to verify.
        return (me.name, child.parent_ref.name, result)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

async def test_invoke_one_shot():
    r = await runtime_manager.invoke(Echo(), task="hi")
    assert r == "echo:hi", r
    assert runtime_manager.list() == [], "invoke must clean up its ref"


async def test_invoke_with_explicit_name():
    r = await runtime_manager.invoke(Echo(), name="my-echo", task="hi")
    assert r == "echo:hi", r
    # And the named ref is gone after invoke completes.
    assert runtime_manager.get("my-echo") is None


async def test_spawn_ask_stop_lifecycle():
    ref = await runtime_manager.spawn(Echo())
    assert ref.status == AgentStatus.RUNNING
    assert runtime_manager.get(ref.name) is ref

    r = await runtime_manager.ask(ref, TaskMessage(task="ping"))
    assert r == "echo:ping"

    await runtime_manager.stop(ref)
    assert ref.status == AgentStatus.STOPPED
    assert runtime_manager.get(ref.name) is None


async def test_send_to_stopped_raises():
    ref = await runtime_manager.spawn(Echo())
    await runtime_manager.stop(ref)
    try:
        await runtime_manager.send(ref, TaskMessage(task="ghost"))
    except AgentDeadError:
        return
    raise AssertionError("send to STOPPED ref must raise AgentDeadError")


async def test_spawn_name_collision():
    a = await runtime_manager.spawn(Echo(), name="dup")
    try:
        await runtime_manager.spawn(Echo(), name="dup")
    except ValueError:
        return
    finally:
        await runtime_manager.stop(a)
    raise AssertionError("name collision must raise ValueError")


async def test_parent_ref_propagation():
    parent = await runtime_manager.spawn(Echo(), name="dad")
    child = await runtime_manager.spawn(Echo(), name="son", parent_ref=parent)
    assert child.parent_ref is parent
    assert parent.parent_ref is None
    await runtime_manager.stop(child)
    await runtime_manager.stop(parent)


async def test_current_ref_inside_pump():
    me_name, child_parent_name, result = await runtime_manager.invoke(Spawner(), task="x")
    assert me_name == child_parent_name, (
        f"child's parent_ref.name ({child_parent_name}) must equal parent's own ref ({me_name})"
    )
    assert result == "echo:hi"


async def test_ask_timeout():
    ref = await runtime_manager.spawn(Slow(delay=0.5))
    try:
        await runtime_manager.ask(ref, TaskMessage(task="x"), timeout=0.05)
    except asyncio.TimeoutError:
        return
    finally:
        await runtime_manager.stop(ref, drain=False)
    raise AssertionError("ask must raise TimeoutError when timeout elapses")


async def test_task_exception_does_not_kill_pump():
    ref = await runtime_manager.spawn(Crashy())
    try:
        await runtime_manager.ask(ref, TaskMessage(task="boom"))
    except ValueError:
        pass
    else:
        raise AssertionError("ask should re-raise the agent's ValueError")

    assert ref.status == AgentStatus.RUNNING, (
        "pump must survive a per-task exception so the next message can be processed"
    )
    r = await runtime_manager.ask(ref, TaskMessage(task="ok"))
    assert r == "ok:ok"
    await runtime_manager.stop(ref)


async def test_shutdown_clears_all():
    a = await runtime_manager.spawn(Echo(), name="sd-a")
    b = await runtime_manager.spawn(Echo(), name="sd-b")
    assert len(runtime_manager.list()) >= 2
    await runtime_manager.shutdown()
    assert runtime_manager.list() == []


async def test_repr_one_liner():
    ref = await runtime_manager.spawn(Echo(), name="repr-check")
    try:
        s = repr(ref)
        assert s.startswith("AgentRef(") and "name='repr-check'" in s and "running" in s, s
        # str() must match repr() — verifies we override Pydantic v2's default.
        assert str(ref) == s
    finally:
        await runtime_manager.stop(ref)


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

TESTS = [
    test_invoke_one_shot,
    test_invoke_with_explicit_name,
    test_spawn_ask_stop_lifecycle,
    test_send_to_stopped_raises,
    test_spawn_name_collision,
    test_parent_ref_propagation,
    test_current_ref_inside_pump,
    test_ask_timeout,
    test_task_exception_does_not_kill_pump,
    test_shutdown_clears_all,
    test_repr_one_liner,
]


async def main():
    failed = []
    for t in TESTS:
        try:
            await t()
            print(f"  ✓ {t.__name__}")
        except Exception as exc:
            failed.append((t.__name__, exc))
            print(f"  ✗ {t.__name__} — {exc!r}")
    if failed:
        print(f"\n❌ {len(failed)}/{len(TESTS)} failed")
        sys.exit(1)
    print(f"\n✅ {len(TESTS)} runtime tests passed")


if __name__ == "__main__":
    asyncio.run(main())
