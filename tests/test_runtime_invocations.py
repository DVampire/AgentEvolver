import asyncio
from types import SimpleNamespace

import pytest

from agentevolver.runtime.invocation import InvocationRuntime, ResourceClaim


def context(owner):
    return SimpleNamespace(id="one-session", extra={"process_pid": owner})


@pytest.mark.asyncio
async def test_same_resource_serializes_across_modules_but_independent_resources_overlap():
    rt = InvocationRuntime()
    active, peak = 0, 0
    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
    await asyncio.gather(*(rt.invoke(kind, "op", work, claims=(ResourceClaim("shell"),))
                           for kind in ("tool", "environment")))
    assert peak == 1
    peak = 0
    await asyncio.gather(*(rt.invoke("environment", "op", work, claims=(ResourceClaim(key),))
                           for key in ("shell-a", "shell-b")))
    assert peak == 2


@pytest.mark.asyncio
async def test_concurrent_acquisition_constructs_once_and_owner_release_keeps_other_owner():
    rt = InvocationRuntime()
    made, closed = [], []
    async def create():
        await asyncio.sleep(.01)
        value = object()
        made.append(value)
        return value
    a, b = await asyncio.gather(*(rt.bind("environment", "ssh", "1", "a", create, closed.append)
                                  for _ in range(2)))
    other = await rt.bind("environment", "ssh", "1", "b", create, closed.append)
    assert a is b and other is not a and len(made) == 2
    await rt.release(owner="a")
    assert closed == [a]
    await rt.release(owner="a")
    assert closed == [a]
    await rt.release()
    assert closed == [a, other]


@pytest.mark.asyncio
async def test_owner_stop_joins_body_cleanup_without_stopping_sibling():
    rt = InvocationRuntime()
    started, cleaned, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def blocked():
        started.set()
        try:
            await finish.wait()
        finally:
            await asyncio.sleep(.01)
            cleaned.set()
    task = asyncio.create_task(rt.invoke("connector", "request", blocked, ctx=context("a")))
    await started.wait()
    await rt.release(owner="a")
    assert cleaned.is_set()
    with pytest.raises(asyncio.CancelledError):
        await task
    async def answer():
        return 42
    assert await rt.invoke("skill", "read", answer, ctx=context("b")) == 42


@pytest.mark.asyncio
async def test_timeout_in_queue_leaves_no_record_or_resource_lock():
    rt = InvocationRuntime()
    ready, finish = asyncio.Event(), asyncio.Event()
    async def first():
        ready.set()
        await finish.wait()
    claim = (ResourceClaim("one"),)
    task = asyncio.create_task(rt.invoke("tool", "first", first, claims=claim))
    await ready.wait()
    with pytest.raises(asyncio.TimeoutError):
        await rt.invoke("tool", "second", first, claims=claim, timeout=.01)
    finish.set()
    await task
    assert not rt._state().active and not rt._state().waiting


@pytest.mark.asyncio
async def test_nested_conflict_fails_instead_of_deadlocking():
    rt = InvocationRuntime()
    async def child():
        return 1
    async def parent():
        return await rt.invoke("tool", "child", child, claims=(ResourceClaim("same"),))
    with pytest.raises(RuntimeError, match="ancestor"):
        await asyncio.wait_for(rt.invoke("tool", "parent", parent,
                                        claims=(ResourceClaim("same"),)), .5)


def test_shared_reads_and_parent_paths():
    assert not ResourceClaim("x", True).conflicts(ResourceClaim("x", True))
    assert ResourceClaim.path("/tmp/project").conflicts(ResourceClaim.path("/tmp/project/a"))
    assert not ResourceClaim.path("/tmp/project").conflicts(ResourceClaim.path("/tmp/project2"))


@pytest.mark.asyncio
async def test_environment_manager_isolates_instance_state_and_versions(tmp_path):
    from pydantic import Field, PrivateAttr
    from agentevolver.environment.context import EnvironmentContextManager
    from agentevolver.environment.server import environment_manager
    from agentevolver.environment.types import Environment, EnvironmentConfig, EnvironmentContext
    from agentevolver.runtime.invocation import CURRENT_RUNTIME

    closed = []
    class Store(Environment):
        name: str = "test_store"
        description: str = "Owner-local state"
        metadata: dict = Field(default_factory=dict)
        _values: dict = PrivateAttr(default_factory=dict)

        @environment_manager.action(name="put", read_only=False, destructive=False)
        async def put(self, key, value, ctx=None):
            await asyncio.sleep(.01)
            self._values[key] = value
            return {"success": True, "message": value}

        async def get_state(self, ctx=None):
            return dict(self._values)

        async def cleanup(self):
            closed.append(dict(self._values))

    rt = InvocationRuntime()
    token = CURRENT_RUNTIME.set(rt)
    manager = EnvironmentContextManager(base_dir=str(tmp_path))
    instance = Store()
    info = EnvironmentConfig(name=instance.name, cls=Store, instance=instance,
                             actions=instance.actions, version="1", permission_mode="danger_full_access",
                             description="test", rules="Use owner-local state")
    manager._environment_configs[instance.name] = info
    a = EnvironmentContext(id="shared-session", extra={"process_pid": "a"})
    b = EnvironmentContext(id="shared-session", extra={"process_pid": "b"})
    try:
        results = await asyncio.gather(
            manager(instance.name, "put", {"key": "x", "value": "A"}, a),
            manager(instance.name, "put", {"key": "x", "value": "B"}, b),
        )
        assert all(r.success for r in results), [r.message for r in results]
        assert await manager.get_state(instance.name, a) == {"x": "A"}
        assert await manager.get_state(instance.name, b) == {"x": "B"}
        assert a.input == b.input == {}
        old = await manager.get(instance.name, ctx=b)
        replacement = info.model_copy(update={"version": "2"})
        manager._environment_configs[instance.name] = replacement
        assert await manager.get(instance.name, ctx=b) is not old
        assert old._values == {"x": "B"}
        await rt.release(owner="a")
        assert closed == [{"x": "A"}]
    finally:
        await rt.release()
        CURRENT_RUNTIME.reset(token)


@pytest.mark.asyncio
async def test_kernel_cleans_bound_resources_if_start_hook_fails():
    from agentevolver.runtime.kernel import Kernel
    from agentevolver.runtime.invocation import runtime
    from agentevolver.session import SessionContext
    cleaned = []
    class FailsBeforeTurn:
        async def on_start(self, task, proc):
            async def create():
                return object()
            await runtime().bind("environment", "ssh", "1", proc.pid, create, cleaned.append)
            raise ValueError("startup failed")
        async def __call__(self, **kwargs):
            raise AssertionError("must not enter turn")
    kernel = Kernel()
    proc = await kernel.spawn(FailsBeforeTurn(), ctx=SessionContext(id="startup"))
    await kernel.wait(proc)
    assert proc.error and len(cleaned) == 1
    assert not proc.cleanup_errors
    await kernel.shutdown()


@pytest.mark.asyncio
async def test_runtime_capacity_is_real_and_cancellation_of_creation_does_not_publish():
    rt = InvocationRuntime()
    active = peak = 0
    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
    await asyncio.gather(*(rt.invoke("environment", "evaluate", work, max_concurrency=2)
                           for _ in range(5)))
    assert peak == 2
    entered = asyncio.Event()
    async def create():
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(rt.bind("environment", "ssh", "1", "a", create, lambda _: None))
    await entered.wait()
    await rt.release(owner="a")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not rt._state().bindings


@pytest.mark.asyncio
async def test_plugin_owner_instances_receive_context_and_close_independently(tmp_path):
    from agentevolver.plugins.context import PluginContextManager
    from agentevolver.plugins.types import Plugin, PluginConfig, PluginTool
    from agentevolver.response.types import Response
    from agentevolver.runtime.invocation import CURRENT_RUNTIME, current_owner
    closed = []
    class ReadOwner(PluginTool):
        name: str = "read_owner"
        async def __call__(self):
            await asyncio.sleep(.01)
            return Response(type="tool", success=True, message=current_owner())
    class Client(Plugin):
        name: str = "isolated_client"
        state_scope: str = "owner"
        concurrent: bool = True
        tools = (ReadOwner,)
        async def cleanup(self):
            closed.append(id(self))
    manager = PluginContextManager(base_dir=str(tmp_path))
    manager._plugin_configs["isolated_client"] = PluginConfig(
        name="isolated_client", cls=Client, instance=Client())
    rt = InvocationRuntime()
    token = CURRENT_RUNTIME.set(rt)
    try:
        a, b = await asyncio.gather(*(manager("isolated_client", "read_owner", ctx=context(owner))
                                    for owner in ("a", "b")))
        assert [a.message, b.message] == ["a", "b"]
        await rt.release(owner="a")
        assert len(closed) == 1
        await rt.release(owner="b")
        assert len(set(closed)) == 2
    finally:
        await rt.release()
        CURRENT_RUNTIME.reset(token)


@pytest.mark.asyncio
async def test_connector_reentrant_contract_allows_overlapping_requests(tmp_path, monkeypatch):
    from agentevolver.connector.context import ConnectorContextManager
    from agentevolver.connector.types import ConnectorConfig
    from agentevolver.response.types import Response
    from agentevolver.runtime.invocation import CURRENT_RUNTIME
    manager = ConnectorContextManager(base_dir=str(tmp_path))
    cfg = ConnectorConfig(name="data", actions=["fetch"], permission_mode="read_only",
        metadata={"concurrent": True}, action_annotations={"fetch": {"readOnlyHint": True}})
    manager._connector_configs[cfg.name] = cfg
    active = peak = 0
    async def request(cfg, action, args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        return Response(type="tool", success=True, message=str(args["n"]))
    monkeypatch.setattr(manager, "_invoke_mcp", request)
    rt = InvocationRuntime()
    token = CURRENT_RUNTIME.set(rt)
    try:
        results = await asyncio.gather(*(manager("data", "fetch", {"n": n}, ctx=context("a"))
                                         for n in range(3)))
        assert all(value.success for value in results)
        assert peak == 3
        assert len([x for x in rt.snapshot() if x["module"] == "connector"]) == 3
    finally:
        CURRENT_RUNTIME.reset(token)


@pytest.mark.asyncio
async def test_nested_capacity_and_cross_branch_resource_cycles_fail_without_hanging():
    rt = InvocationRuntime()
    async def child():
        return 1
    async def parent():
        return await rt.invoke("environment", "evaluate", child, max_concurrency=1)
    with pytest.raises(RuntimeError, match="deadlock"):
        await asyncio.wait_for(rt.invoke("environment", "evaluate", parent, max_concurrency=1), .5)
    started = [asyncio.Event(), asyncio.Event()]
    async def branch(index):
        started[index].set()
        await started[1 - index].wait()
        return await rt.invoke("tool", "nested", child, claims=(ResourceClaim(str(1 - index)),))
    results = await asyncio.wait_for(asyncio.gather(*(
        rt.invoke("tool", "outer", lambda index=i: branch(index), claims=(ResourceClaim(str(i)),))
        for i in range(2)), return_exceptions=True), .5)
    assert any(isinstance(result, RuntimeError) for result in results)
    assert not rt._state().active


@pytest.mark.asyncio
async def test_shutdown_deadline_retains_cleanup_until_joined():
    rt = InvocationRuntime()
    entered, finish = asyncio.Event(), asyncio.Event()
    async def close(value):
        entered.set()
        await finish.wait()
    rt.own("environment", "desktop", "a", object(), close)
    remaining = await rt.shutdown(timeout=.01)
    assert entered.is_set() and remaining and rt._state().bindings
    finish.set()
    assert await rt.shutdown(timeout=1) == []
    assert not rt._state().bindings


@pytest.mark.asyncio
async def test_nested_owner_stop_does_not_interrupt_child_finally_twice():
    rt = InvocationRuntime()
    entered, cleaned = asyncio.Event(), asyncio.Event()
    async def child():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(.01)
            cleaned.set()
    async def parent():
        await rt.invoke("connector", "child", child, ctx=context("a"))
    task = asyncio.create_task(rt.invoke("tool", "parent", parent, ctx=context("a")))
    await entered.wait()
    await rt.release(owner="a")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_workflow_agent_nodes_use_fresh_kernel_processes_and_join_cancellation(tmp_path, monkeypatch):
    import agentevolver.runtime as runtime_module
    from agentevolver.agent.context.manager import AgentContextManager
    from agentevolver.agent.server import agent_manager
    from agentevolver.runtime.kernel import Kernel
    from agentevolver.workflow.runtime import WorkflowRuntime
    from agentevolver.workflow.types import StepType
    from agentevolver.session import SessionContext
    from unittest.mock import AsyncMock

    entered, finish = asyncio.Event(), asyncio.Event()
    seen, closed = [], []
    class Program:
        name = "test_program"
        def fresh(self):
            return Program()
        async def __call__(self, **kwargs):
            seen.append(self.proc)
            if len(seen) == 2:
                entered.set()
            try:
                await finish.wait()
                return {"success": True}
            finally:
                closed.append(self.proc.pid)

    kernel = Kernel()
    monkeypatch.setattr(runtime_module, "kernel", kernel)
    manager = AgentContextManager(base_dir=str(tmp_path))
    monkeypatch.setattr(manager, "get_info", AsyncMock(return_value=SimpleNamespace(version="1", instance=Program())))
    monkeypatch.setattr(agent_manager, "agent_context_manager", manager)
    workflow = WorkflowRuntime()
    ctx = SessionContext(id="same-parent")
    tasks = [asyncio.create_task(workflow._invoke(StepType.AGENT, "test_program", "work", {}, ctx, 0))
             for _ in range(2)]
    try:
        await asyncio.sleep(.05)
        assert not any(task.done() for task in tasks), [task.exception() for task in tasks if task.done()]
        await asyncio.wait_for(entered.wait(), 2)
        assert len({proc.pid for proc in seen}) == 2
        assert len({proc.ctx.id for proc in seen}) == 2
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert len(closed) == 1 and not tasks[1].done()
        finish.set()
        await tasks[1]
        assert len(closed) == 2 and all(proc._exited.is_set() for proc in seen)
    finally:
        finish.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await kernel.shutdown()


def test_gateway_relay_keeps_two_owners_of_same_environment_separate():
    from agentevolver.gateway.service import AgentGateway
    gateway = AgentGateway()
    views = [gateway._relayed_view(dict(env_name="browser", session_id="s", owner_id=owner,
                                       type="vnc", url=f"ws://{owner}/view")) for owner in ("a", "b")]
    assert views[0]["url"] != views[1]["url"]
    assert set(gateway._vnc_targets.values()) == {"ws://a/view", "ws://b/view"}


@pytest.mark.asyncio
async def test_agent_manager_children_retain_kernel_parent_and_one_budget(tmp_path, monkeypatch):
    import agentevolver.runtime as runtime_module
    from agentevolver.agent.context.manager import AgentContextManager
    from agentevolver.runtime.kernel import Kernel
    from agentevolver.session import SessionContext
    from unittest.mock import AsyncMock
    kernel = Kernel()
    monkeypatch.setattr(runtime_module, "kernel", kernel)
    manager = AgentContextManager(base_dir=str(tmp_path))
    seen, together = [], asyncio.Event()
    class Child:
        name = "child"
        def fresh(self):
            return Child()
        async def __call__(self, **kwargs):
            seen.append(self.proc)
            if len(seen) == 2:
                together.set()
            await together.wait()
            return {"success": True}
    monkeypatch.setattr(manager, "get_info", AsyncMock(return_value=SimpleNamespace(version="1", instance=Child())))
    class Parent:
        name = "parent"
        max_token = 1234
        async def __call__(self, **kwargs):
            await asyncio.gather(*(manager("child", {"task": "work"}, ctx=self.proc.ctx) for _ in range(2)))
            return {"success": True}
    proc = await kernel.spawn(Parent(), ctx=SessionContext(id="dispatch-parent"))
    try:
        await asyncio.wait_for(kernel.wait(proc), 2)
        assert not proc.error, proc.error
        assert len(seen) == 2
        assert all(child.parent_pid == proc.pid and child.budget is proc.budget for child in seen)
    finally:
        await kernel.shutdown()
