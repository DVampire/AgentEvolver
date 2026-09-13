"""Public admission and actual owner state; local workers/Chromium, mocked remote transports."""
import asyncio
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pydantic import Field

from agentevolver.environment.context import EnvironmentContextManager
from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment, EnvironmentConfig, EnvironmentContext
from agentevolver.runtime.invocation import CURRENT_RUNTIME, InvocationRuntime, ResourceClaim


def ctx(owner, root=None):
    return EnvironmentContext(id="concurrency-study", workspace_root=str(root) if root else None,
                              extra={"process_pid": owner})


def mount(manager, instance, **config):
    from agentevolver.permission import permission_manager, PermissionMode
    permission_manager.register(instance.name, mode=PermissionMode.DANGER_FULL_ACCESS)
    manager._environment_configs[instance.name] = EnvironmentConfig(
        name=instance.name, cls=type(instance), instance=instance, actions=instance.actions,
        config={"name": instance.name, **config}, version="1", description="test", rules="test",
        permission_mode="danger_full_access")


@pytest_asyncio.fixture
async def calls(tmp_path, monkeypatch):
    from agentevolver.permission import permission_manager
    from agentevolver.permission.context import PermissionContextManager
    monkeypatch.setattr(permission_manager, "_ctx", PermissionContextManager())
    runtime = InvocationRuntime()
    token = CURRENT_RUNTIME.set(runtime)
    manager = EnvironmentContextManager(base_dir=str(tmp_path))
    # Explicit approval for local test operations; remote transports are replaced.
    manager._execution_pipeline.set_approval_resolver(lambda execution, reason: True)
    try:
        from agentevolver.paths import path_manager
        with path_manager.workspace(tmp_path):
            yield runtime, manager
    finally:
        await runtime.release()
        CURRENT_RUNTIME.reset(token)


@pytest.mark.asyncio
async def test_executable_skill_template_orders_owner_actions_and_isolates_owners(calls, monkeypatch):
    from agentevolver.registry import ENVIRONMENT
    # Loading the actual shipped template also exercises the decorator's native keyword API.
    monkeypatch.setattr(ENVIRONMENT, "_module_dict", dict(ENVIRONMENT._module_dict))
    path = Path(__file__).parents[1] / "agentevolver/skill/evolving/self_evolving_skill/references/environment/template.py"
    cls = runpy.run_path(str(path))["MyEnvironment"]
    runtime, manager = calls
    env = cls()
    mount(manager, env)
    assert "parallel_safe" not in env.actions["set_value"].metadata
    assert env.actions["set_value"].metadata["read_only"] is False
    results = await asyncio.gather(*(
        manager(env.name, "set_value", {"key": "k", "value": owner}, ctx(owner))
        for owner in ("a", "b")))
    assert all(r.success for r in results)
    results = await asyncio.gather(*(manager(env.name, "get_value", {"key": "k"}, ctx(owner)) for owner in ("a", "b")))
    assert [r.data["data"]["value"] for r in results] == ["a", "b"]
    # The plain template serializes all state on one owner, but other owners proceed.
    entered, finish = asyncio.Event(), asyncio.Event()
    async def hold():
        entered.set()
        await finish.wait()
    held = asyncio.create_task(runtime.invoke("environment", "held-set", hold, ctx=ctx("a"),
        claims=env.resource_claims(ctx("a"), {"key": "k"}, "set_value")))
    await entered.wait()
    blocked = asyncio.create_task(manager(env.name, "get_value", {"key": "k"}, ctx("a")))
    try:
        independent = await asyncio.wait_for(manager(env.name, "set_value", {"key": "other", "value": "ok"}, ctx("b")), 1)
        assert independent.success and not blocked.done()
    finally:
        finish.set()
        await held
        await blocked


@pytest.mark.asyncio
async def test_two_numerical_engines_share_capacity_and_match_uncached_serial_results(calls, tmp_path):
    class Numerical(Environment):
        name: str = "numerical"
        description: str = "Local process fixture, not a financial engine"
        metadata: dict = Field(default_factory=dict)
        max_concurrency: int = 2
        concurrency_group: str = "test-research-workers"

        state_scope: str = "call"

        @environment_manager.action(name="evaluate", write_paths=("output",), read_only=False,
                                    destructive=False, open_world=False)
        async def evaluate(self, n: int, output: str, ctx=None):
            code = ("import json,sys,time; start=time.monotonic(); n=int(sys.argv[1]); "
                    "value=sum(i*i for i in range(n)); time.sleep(.12); "
                    "print(json.dumps(dict(value=value,start=start,end=time.monotonic())))")
            proc = await asyncio.create_subprocess_exec(sys.executable, "-c", code, str(n), stdout=asyncio.subprocess.PIPE)
            try:
                stdout, _ = await proc.communicate()
                assert proc.returncode == 0
                data = json.loads(stdout)
                Path(output).write_text(json.dumps(data))
                return {"success": True, "message": "computed", "data": data}
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()

    _, manager = calls
    for name in ("factor_fixture", "strategy_fixture"):
        mount(manager, Numerical(name=name))
    async def evaluate(i, mode):
        return await manager(("factor_fixture", "strategy_fixture")[i % 2], "evaluate",
            {"n": 1000 + i, "output": str(tmp_path / f"{mode}-{i}.json")}, ctx("research", tmp_path))
    serial = [await evaluate(i, "serial") for i in range(4)]
    parallel = await asyncio.gather(*(evaluate(i, "parallel") for i in range(4)))
    assert all(r.success for r in serial + parallel), [r.message for r in serial + parallel]
    assert [r.data["data"]["value"] for r in serial] == [r.data["data"]["value"] for r in parallel]
    edges = sorted((r.data["data"][k], delta) for r in parallel for k, delta in (("start", 1), ("end", -1)))
    active = peak = 0
    for _, delta in edges:
        active += delta
        peak = max(peak, active)
    assert peak == 2  # Actual child-process intervals, not queued-call timestamps.


@pytest.mark.asyncio
async def test_evaluation_template_native_overlap_replay_and_failure(calls, tmp_path, monkeypatch):
    import threading
    from agentevolver.registry import ENVIRONMENT
    monkeypatch.setattr(ENVIRONMENT, "_module_dict", dict(ENVIRONMENT._module_dict))
    path = Path(__file__).parents[1] / "agentevolver/skill/evolving/self_evolving_skill/references/environment/template-evaluation.py"
    template = runpy.run_path(str(path))
    cls = template["MyEvaluationEnvironment"]
    _, manager = calls
    mount(manager, cls())
    source = tmp_path / "input.json"
    source.write_text('{"values": [1, 2, 3]}')
    barrier = threading.Barrier(2, timeout=3)
    original = template["math"].fsum
    def together(values):
        barrier.wait()
        return original(values)
    monkeypatch.setattr(template["math"], "fsum", together)
    async def evaluate(result, input_path=source):
        return await manager(cls().name, "evaluate", {
            "input_path": str(input_path), "result_path": str(result)}, ctx("a"))
    # The shipped synchronous business method enters two threads through native admission.
    outputs = [tmp_path / f"result-{i}.json" for i in range(2)]
    responses = await asyncio.gather(*(evaluate(p) for p in outputs))
    assert all(r.success for r in responses), [r.message for r in responses]
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert json.loads(outputs[0].read_text())["value"] == 6
    monkeypatch.setattr(template["math"], "fsum", original)
    assert (await evaluate(outputs[0])).data["data"]["cached"] is True
    before = outputs[0].read_bytes()
    source.write_text('{"values": [10]}')
    assert not (await evaluate(outputs[0])).success
    assert outputs[0].read_bytes() == before
    assert not (await evaluate(tmp_path / "absent-result", tmp_path / "absent-input")).success
    source.write_text('{"values": [true]}')
    assert not (await evaluate(tmp_path / "invalid-result")).success
    assert not (tmp_path / "invalid-result").exists()
    assert not (await evaluate(source)).success


@pytest.mark.asyncio
async def test_capacity_exempt_status_still_waits_for_result_write_claim(calls, tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()
    class Trial(Environment):
        name: str = "status_path_fixture"
        description: str = "Locked result reader fixture"
        metadata: dict = Field(default_factory=dict)
        state_scope: str = "call"
        max_concurrency: int = 1
        @environment_manager.action(name="evaluate", read_only=False, write_paths=("output_dir",))
        async def evaluate(self, output_dir: str, **kwargs):
            entered.set()
            await finish.wait()
            Path(output_dir, "result.json").write_text('{"complete": true}')
            return {"success": True, "message": "done"}
        @environment_manager.action(name="status", read_only=True, capacity_exempt=True,
                                    read_paths=("output_dir",))
        async def status(self, output_dir: str, **kwargs):
            return {"success": True, "message": Path(output_dir, "result.json").read_text()}
    _, manager = calls
    mount(manager, Trial())
    args = {"output_dir": str(tmp_path)}
    worker = asyncio.create_task(manager("status_path_fixture", "evaluate", args, ctx("a")))
    await asyncio.wait_for(entered.wait(), 2)
    reader = asyncio.create_task(manager("status_path_fixture", "status", args, ctx("a")))
    try:
        done, _ = await asyncio.wait([reader], timeout=.05)
        assert not done  # Capacity exemption cannot turn a locked output into live status.
    finally:
        finish.set()
        result, observed = await asyncio.gather(worker, reader)
    assert result.success and observed.success and '"complete": true' in observed.message


@pytest.mark.asyncio
async def test_published_features_feed_consumer_before_diagnostics_finish(calls, tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()
    class Stages(Environment):
        name: str = "stages_fixture"
        description: str = "Feature readiness fixture"
        metadata: dict = Field(default_factory=dict)
        state_scope: str = "call"
        max_concurrency: int = 2
        @environment_manager.action(name="materialize", read_only=False, write_paths=("features",))
        async def materialize(self, features: str, **kwargs):
            Path(features).write_text('[1, 2, 3]')
            return {"success": True, "message": "features ready"}
        @environment_manager.action(name="diagnose", read_only=False,
                                    read_paths=("features",), write_paths=("output",))
        async def diagnose(self, features: str, output: str, **kwargs):
            entered.set()
            await finish.wait()
            Path(output).write_text('diagnostics complete')
            return {"success": True, "message": "diagnosed"}
        @environment_manager.action(name="simulate", read_only=False,
                                    read_paths=("features",), write_paths=("output",))
        async def simulate(self, features: str, output: str, **kwargs):
            Path(output).write_text(str(sum(json.loads(Path(features).read_text()))))
            return {"success": True, "message": "simulated"}
    _, manager = calls
    mount(manager, Stages())
    features = str(tmp_path / "features.json")
    assert (await manager("stages_fixture", "materialize", {"features": features}, ctx("a"))).success
    work = asyncio.create_task(manager("stages_fixture", "diagnose", {
        "features": features, "output": str(tmp_path / "diagnostics")}, ctx("a")))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        consumer = await asyncio.wait_for(manager("stages_fixture", "simulate", {
            "features": features, "output": str(tmp_path / "strategy")}, ctx("a")), 2)
        assert consumer.success and not work.done()
        assert (tmp_path / "strategy").read_text() == "6"
    finally:
        finish.set()
        await work


@pytest.mark.asyncio
async def test_call_instances_cleanup_after_success_failure_and_state_observation(calls):
    initialized, cleaned = [], []

    class Trial(Environment):
        name: str = "trial"
        description: str = "Independent business methods"
        metadata: dict = Field(default_factory=dict)
        state_scope: str = "call"

        async def initialize(self):
            self.values = []
            initialized.append(self)

        async def cleanup(self):
            cleaned.append(self)

        async def get_state(self, ctx=None, **kwargs):
            return {"success": True, "state": list(self.values)}

        @environment_manager.action(name="evaluate", read_only=False, open_world=False,
                                    destructive=False)
        async def evaluate(self, value: str, ctx=None):
            self.values.append(value)
            await asyncio.sleep(.01)
            if value == "bad":
                raise ValueError("invalid trial")
            return {"success": True, "data": list(self.values)}

    runtime, manager = calls
    mount(manager, Trial())
    responses = await asyncio.gather(*(manager("trial", "evaluate", {"value": value}, ctx("same"))
                                       for value in ("a", "bad", "b")))
    assert [r.success for r in responses] == [True, False, True]
    assert responses[0].data["data"] == ["a"] and responses[2].data["data"] == ["b"]
    state = await manager.get_state("trial", ctx("same"))
    assert state["state"] == []
    assert len(initialized) == len(cleaned) == 4
    assert {id(v) for v in initialized} == {id(v) for v in cleaned}
    assert await manager.live_view("trial", ctx("same")) is None
    assert len(initialized) == 4
    await runtime.release(owner="same")
    assert len(cleaned) == 4  # Call cleanup must not run again at owner exit.


@pytest.mark.asyncio
async def test_sync_trial_cancel_joins_thread_and_cleanup_before_conflicting_writer(calls, tmp_path):
    import threading
    started, finish = threading.Event(), threading.Event()
    cleaning, finish_cleanup = asyncio.Event(), asyncio.Event()
    cleaned = []

    class Trial(Environment):
        name: str = "sync_trial"
        description: str = "Blocking business method; no runtime imports"
        metadata: dict = Field(default_factory=dict)
        state_scope: str = "call"
        max_concurrency: int = 2

        @environment_manager.action(name="evaluate", read_only=False, destructive=False,
                                    open_world=False, write_paths=("output",))
        def evaluate(self, output: str, wait: bool = False, ctx=None):
            self.waited = wait
            if wait:
                started.set()
                assert finish.wait(5)
            Path(output).write_text("result")
            return {"success": True, "data": {"path": output}}

        async def cleanup(self):
            if self.waited:
                cleaning.set()
                await finish_cleanup.wait()
            cleaned.append(self)

    _, manager = calls
    mount(manager, Trial())
    first = asyncio.create_task(manager("sync_trial", "evaluate", {"output": "same.json", "wait": True}, ctx("a", tmp_path)))
    second = None
    try:
        await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
        assert started.is_set()
        first.cancel()
        # Unrelated work progresses while cancellation is waiting for a blocking method.
        independent = await asyncio.wait_for(manager("sync_trial", "evaluate", {"output": "other.json"}, ctx("a", tmp_path)), 1)
        assert independent.success and Path(independent.data["data"]["path"]).is_absolute()
        second = asyncio.create_task(manager("sync_trial", "evaluate", {"output": "same.json"}, ctx("b", tmp_path)))
        await asyncio.sleep(.02)
        assert not first.done() and not second.done()
        first.cancel()  # Repeated cancellation must not detach the thread.
        finish.set()
        await asyncio.wait_for(cleaning.wait(), 1)
        assert not second.done()
        finish_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await asyncio.wait_for(second, 1)).success
        assert len(cleaned) == 3
    finally:
        finish.set()
        finish_cleanup.set()
        await asyncio.gather(first, *( [second] if second else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_call_scope_retries_failed_cleanup_at_owner_exit(calls):
    closes = []

    class Trial(Environment):
        name: str = "cleanup_trial"
        description: str = "Retryable cleanup"
        metadata: dict = Field(default_factory=dict)
        state_scope: str = "call"

        @environment_manager.action(name="evaluate", read_only=True, open_world=False)
        async def evaluate(self, ctx=None):
            return {"success": True}

        async def cleanup(self):
            closes.append(self)
            if len(closes) == 1:
                raise RuntimeError("backend temporarily unavailable")

    runtime, manager = calls
    mount(manager, Trial())
    response = await manager("cleanup_trial", "evaluate", {}, ctx("a"))
    assert not response.success
    await runtime.release(owner="a")
    assert len(closes) == 2 and closes[0] is closes[1]


@pytest.mark.asyncio
async def test_failed_initialization_retains_unreleased_resource_for_retry(calls):
    closes = []

    class Trial(Environment):
        name: str = "initialization_trial"
        description: str = "Partial acquisition"
        metadata: dict = Field(default_factory=dict)
        state_scope: str = "call"

        async def initialize(self):
            raise RuntimeError("partial initialization failed")

        async def cleanup(self):
            closes.append(self)
            if len(closes) == 1:
                raise RuntimeError("backend not yet released")

        @environment_manager.action(name="evaluate", read_only=True, open_world=False)
        async def evaluate(self, ctx=None):
            pytest.fail("partially initialized environments must not execute")

    runtime, manager = calls
    mount(manager, Trial())
    response = await manager("initialization_trial", "evaluate", {}, ctx("a"))
    assert not response.success
    await runtime.release(owner="a")
    assert len(closes) == 2 and closes[0] is closes[1]


@pytest.mark.asyncio
async def test_agent_batch_uses_environment_state_contract_without_parallel_flag(calls, monkeypatch):
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.agent.loop.executor import ActionExecutor
    from agentevolver.agent.loop.decision import ActionCall, ActionResult
    from agentevolver.capability import COMPONENT_TYPES

    entered = []
    both = asyncio.Event()

    class Trial(Environment):
        name: str = "batch_trial"
        description: str = "Parallel business actions"
        metadata: dict = Field(default_factory=dict)
        state_scope: str = "call"

        @environment_manager.action(name="evaluate", read_only=False, destructive=False, open_world=False)
        async def evaluate(self, value: int, ctx=None):
            entered.append(value)
            if len(entered) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 1)
            return {"success": True, "data": value}

    _, manager = calls
    mount(manager, Trial())
    routing = {"batch_trial__evaluate": ("environment", "batch_trial", "evaluate")}

    async def schemas(*args, **kwargs):
        return [], routing

    monkeypatch.setattr("agentevolver.agent.context.capabilities.assemble_native_tools", schemas)
    families = [SimpleNamespace(type=f.type, manager=lambda: manager) if f.type == "environment" else f
                for f in COMPONENT_TYPES]
    monkeypatch.setattr("agentevolver.capability.COMPONENT_TYPES", families)
    agent = SimpleNamespace(capability_allowlists={}, accepts_evolved=[])
    context = ctx("same")
    router = CapabilityRouter()
    await router.schemas(agent, context)
    actions = [ActionCall(id=f"eval-{i}", name="batch_trial__evaluate", args={"value": i}) for i in (1, 2)]
    assert all(router.parallel_safe(action, routing) for action in actions)

    # Real Agent batch admission, with only the unrelated execution side effects stubbed.
    async def invoke(call, **kwargs):
        response = await manager("batch_trial", "evaluate", call.args, context)
        return ActionResult(call=call, output=response.message, error=None if response.success else response.message)

    monkeypatch.setattr(router, "invoke", invoke)
    results = await ActionExecutor(router).run(actions, agent=agent, ctx=context, routing=routing)
    assert entered == [1, 2] and all(not result.error for result in results)


def test_batch_policy_supports_lazy_clients_and_explicit_legacy_override():
    from agentevolver.runtime.invocation import allows_parallel
    from agentevolver.plugins.types import Plugin

    class Client(Plugin):
        concurrent: bool = True

    info = SimpleNamespace(instance=None, cls=Client, config={}, metadata={})
    assert allows_parallel("plugin", info)
    info.config = {"concurrent": False}
    assert not allows_parallel("plugin", info)
    info.metadata = {"parallel_safe": False}
    info.config = {"concurrent": True}
    assert not allows_parallel("plugin", info)


@pytest.mark.asyncio
async def test_job_wait_does_not_block_kill_and_other_owner_remains_live(calls):
    from agentevolver.environment.default.job.environment import JobEnvironment
    from agentevolver.job import job_manager
    runtime, manager = calls
    mount(manager, JobEnvironment())
    tasks = []
    async def launch():
        task = asyncio.create_task(asyncio.Event().wait())
        tasks.append(task)
        return job_manager.register(type="test", label="owned", session_id="concurrency-study", handle=task)
    a = await runtime.invoke("tool", "launch", launch, ctx=ctx("a"))
    b = await runtime.invoke("tool", "launch", launch, ctx=ctx("b"))
    waiter = asyncio.create_task(manager("job", "wait", {"job_ids": [a.id], "condition": "finished", "timeout": 5}, ctx("a")))
    try:
        await asyncio.sleep(.03)
        result = await asyncio.wait_for(manager("job", "kill", {"job_id": a.id}, ctx("a")), 1)
        assert result.success and tasks[0].done() and not tasks[1].done()
        await asyncio.wait_for(waiter, 1)
        foreign = await manager("job", "kill", {"job_id": b.id}, ctx("a"))
        assert not foreign.success and not tasks[1].done()
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.asyncio
async def test_kernel_start_hook_jobs_are_owned_and_reaped():
    from agentevolver.runtime.kernel import Kernel
    from agentevolver.session import SessionContext
    from agentevolver.job import job_manager
    jobs = []
    class Actor:
        async def on_start(self, task, proc):
            jobs.append(job_manager.schedule(session_id=proc.ctx.id, prompt="later", after_seconds=60))
        async def __call__(self, **kwargs):
            return {"success": True}
    kernel = Kernel()
    try:
        proc = await kernel.spawn(Actor(), ctx=SessionContext(id="start-hook"))
        await kernel.wait(proc)
        assert not proc.error and jobs[0].owner_id == proc.pid
        assert job_manager.get(jobs[0].id) is None
    finally:
        await kernel.shutdown()


@pytest.mark.asyncio
async def test_computer_instances_and_owners_do_not_share_desktops_and_failed_start_releases(calls, monkeypatch):
    from agentevolver.environment.default.computer.environment import ComputerEnvironment
    from agentevolver.sandbox import sandbox_manager
    runtime, manager = calls
    acquired, released = [], []
    async def acquire(*args, **kwargs):
        acquired.append(kwargs["reuse_key"])
        return SimpleNamespace(start_desktop=AsyncMock(), run=AsyncMock(return_value=SimpleNamespace(success=True)))
    monkeypatch.setattr(sandbox_manager, "acquire", acquire)
    monkeypatch.setattr(sandbox_manager, "release", AsyncMock(side_effect=lambda *a, **k: released.append(k["reuse_key"])))
    envs = [ComputerEnvironment(name=name) for name in ("desktop_one", "desktop_two")]
    for env in envs:
        mount(manager, env)
    results = await asyncio.gather(*(manager(env.name, "click", {"x": 1, "y": 2}, ctx(owner))
                                     for env, owner in ((envs[0], "a"), (envs[0], "b"), (envs[1], "a"))))
    assert all(r.success for r in results)
    assert len(set(acquired)) == 3
    await runtime.release(owner="a")
    assert len(released) == 2 and "b" in envs[0]._sandboxes
    broken = SimpleNamespace(start_desktop=AsyncMock(side_effect=RuntimeError("desktop failed")))
    monkeypatch.setattr(sandbox_manager, "acquire", AsyncMock(return_value=broken))
    failed = await manager(envs[0].name, "click", {"x": 1, "y": 2}, ctx("c"))
    assert not failed.success and "c" not in envs[0]._sandboxes
    assert envs[0].backend_key("c") in released


@pytest.mark.asyncio
async def test_godot_authoring_routes_are_owned_even_with_shared_workspace(calls, tmp_path, monkeypatch):
    from agentevolver.environment.default.godot.runtime import DockerRuntime
    from agentevolver.tool.default.workspace.container import container_for
    monkeypatch.delenv("AGENTEVOLVER_HOST_ROOT", raising=False)
    monkeypatch.delenv("AGENTEVOLVER_EXEC_CONTAINER", raising=False)
    monkeypatch.setattr("agentevolver.environment.default.godot.runtime.docker_command", AsyncMock(return_value=""))
    runtime, _ = calls
    async def create():
        value = DockerRuntime(tmp_path, "game-test", "base-test", [])
        await value.prepare_base()
        return value
    a, b = await asyncio.gather(*(runtime.invoke("environment", "prepare", create, ctx=ctx(owner, tmp_path)) for owner in ("a", "b")))
    try:
        assert a.base_name != b.base_name
        assert container_for(str(tmp_path), owner="a")[0] == a.base_name
        assert container_for(str(tmp_path), owner="b")[0] == b.base_name
        await a.close()  # No current caller; cleanup must use the stored owner.
        assert container_for(str(tmp_path), owner="a") is None
        assert container_for(str(tmp_path), owner="b")[0] == b.base_name
    finally:
        await a.close()
        await b.close()


@pytest.mark.asyncio
async def test_artifact_renderer_uses_separate_real_browser_contexts(calls):
    from agentevolver.environment.default.artifact_renderer.environment import ArtifactRendererEnvironment
    _, manager = calls
    env = ArtifactRendererEnvironment(use_sandbox=False, default_wait_ms=0)
    await env.initialize()
    mount(manager, env)
    try:
        results = await asyncio.gather(*(manager(env.name, "render_artifact", {"html": f"<h1>{owner}</h1>"}, ctx(owner)) for owner in ("a", "b")))
        assert all(r.success for r in results), [r.message for r in results]
        assert env._sessions["a"].context is not env._sessions["b"].context
        assert await env._sessions["a"].locator("h1").inner_text() == "a"
        assert await env._sessions["b"].locator("h1").inner_text() == "b"
    finally:
        await env.cleanup()


@pytest.mark.asyncio
async def test_browser_native_calls_isolate_state_and_owner_cleanup(calls, tmp_path, monkeypatch):
    from agentevolver.environment.default.browser.environment import BrowserEnvironment
    from agentevolver.environment.default.browser.service import BrowserService
    monkeypatch.setattr(BrowserService, "_checkpoint_path", staticmethod(lambda _: None))
    runtime, manager = calls
    env = BrowserEnvironment(base_dir=str(tmp_path), headless=True)
    await env._service.start()
    mount(manager, env)
    try:
        results = await asyncio.gather(*(manager(env.name, "command",
            {"code": f"await page.set_content('<h1>{owner}</h1>')"}, ctx(owner)) for owner in ("a", "b")))
        assert all(r.success for r in results), [r.message for r in results]
        a, b = [env._service._sessions[owner] for owner in ("a", "b")]
        assert a["context"] is not b["context"]
        assert await a["page"].locator("h1").inner_text() == "a"
        assert await b["page"].locator("h1").inner_text() == "b"
        await runtime.release(owner="a")
        assert a["page"].is_closed() and not b["page"].is_closed()
        assert "a" not in env._sessions and "b" in env._sessions
    finally:
        await env.cleanup()


@pytest.mark.asyncio
async def test_vnc_browser_uses_common_owner_binding_for_separate_displays(calls, monkeypatch):
    from agentevolver.environment.default.browser.environment import BrowserEnvironment
    from agentevolver.environment.default.browser.service import BrowserService
    monkeypatch.setattr(BrowserService, "start", AsyncMock())
    stopped = []
    async def stop(service):
        stopped.append(service)
    async def view(service):
        return f"ws://display.invalid/{id(service)}"
    monkeypatch.setattr(BrowserService, "stop", stop)
    monkeypatch.setattr(BrowserService, "vnc_ws_url", view)
    runtime, manager = calls
    env = BrowserEnvironment(vnc=True, use_sandbox=True)
    assert not env.managed_sessions
    mount(manager, env, vnc=True, use_sandbox=True)
    info = manager._environment_configs[env.name]
    a, b = await asyncio.gather(*(manager.bound(info, ctx(owner)) for owner in ("a", "b")))
    assert a is not b and a is not env and a._service is not b._service
    assert (await a.live_view()).url != (await b.live_view()).url
    await runtime.release(owner="a")
    assert a._service in stopped and b._service not in stopped


@pytest.mark.asyncio
async def test_ssh_native_calls_isolate_shells_and_view_cleanup(calls, monkeypatch):
    import agentevolver.environment.default.ssh.environment as ssh_module
    from agentevolver.port import port_manager
    created, unregistered = [], []
    class Service:
        def __init__(self, config, key):
            self.key, self.last, self.stopped = key, "", False
            self.start = AsyncMock()
            self.is_alive = AsyncMock(return_value=True)
            self.run_raw = AsyncMock(return_value=ssh_module.SSHResult(0))
            created.append(self)
        async def run(self, command, **kwargs):
            self.last = command
            await asyncio.sleep(.01)
            return ssh_module.SSHResult(0, stdout=self.last)
        async def stop(self):
            self.stopped = True
    monkeypatch.setattr(ssh_module, "SSHService", Service)
    monkeypatch.setattr(port_manager, "unregister", lambda name: unregistered.append(name))
    runtime, manager = calls
    env = ssh_module.SSHEnvironment(host="test.invalid", user="test", live_view=False)
    mount(manager, env)
    owners = ("samehead-a", "samehead-b")
    results = await asyncio.gather(*(manager(env.name, "run", {"command": owner}, ctx(owner)) for owner in owners))
    assert all(r.success for r in results), [r.message for r in results]
    assert [r.data["stdout"] for r in results] == list(owners)
    assert len(created) == 2 and len({s.key for s in created}) == 2
    assert env._job_prefix(ctx(owners[0])) != env._job_prefix(ctx(owners[1]))
    alias = ssh_module.SSHEnvironment(name="other_host", host="test.invalid", user="test")
    assert alias._job_prefix(ctx(owners[0])) != env._job_prefix(ctx(owners[0]))
    view_key = (owners[0], "test.invalid", "shell")
    assert alias._view_port_name(view_key) != env._view_port_name(view_key)
    for owner in owners:
        for kind in ("shell", "view"):
            key = (owner, "test.invalid", kind)
            env._view_ports[key] = 12345
            env._view_remote_ports[key] = 23456
            env._view_urls[key] = "http://test.invalid"
    await runtime.release(owner=owners[0])
    assert created[0].stopped and not created[1].stopped
    assert all(k[0] == owners[1] for k in env._view_urls)
    assert len(unregistered) == 2 and all(owners[0] in name for name in unregistered)
    commands = " ".join(call.args[0] for call in created[0].run_raw.call_args_list)
    assert env._job_prefix(ctx(owners[0])) in commands
    assert env._job_prefix(ctx(owners[1])) not in commands


@pytest.mark.asyncio
async def test_terminal_native_handles_allow_independent_work_and_control(calls, tmp_path):
    from agentevolver.environment.default.terminal.environment import TerminalEnvironment
    from agentevolver.terminal import terminal_manager
    runtime, manager = calls
    mount(manager, TerminalEnvironment(startup_timeout=.01))
    opened = await asyncio.gather(*(manager("terminal", "open", {"command": "cat", "cwd": str(tmp_path)}, ctx(owner, tmp_path))
                                    for owner in ("a", "a", "b")))
    assert all(r.success for r in opened), [r.message for r in opened]
    ids = [r.data["terminal_id"] for r in opened]
    # Hold one PTY's admission: the second PTY and close control must still run.
    entered, finish = asyncio.Event(), asyncio.Event()
    async def hold():
        entered.set()
        await finish.wait()
    held = asyncio.create_task(runtime.invoke("environment", "held-send", hold, ctx=ctx("a"),
        claims=(ResourceClaim(f"terminal:{ids[0]}"),)))
    await entered.wait()
    try:
        second = await asyncio.wait_for(manager("terminal", "send",
            {"terminal_id": ids[1], "text": "independent", "timeout": .05}, ctx("a", tmp_path)), 2)
        assert second.success and "independent" in second.message
        foreign = await manager("terminal", "close", {"terminal_id": ids[2]}, ctx("a"))
        assert not foreign.success and not terminal_manager.get(ids[2]).status.is_final
        closed = await asyncio.wait_for(manager("terminal", "close", {"terminal_id": ids[0]}, ctx("a")), 2)
        assert closed.success and terminal_manager.get(ids[0]) is None
    finally:
        finish.set()
        await held
    await runtime.release(owner="a")
    assert terminal_manager.get(ids[1]) is None and not terminal_manager.get(ids[2]).status.is_final


@pytest.mark.asyncio
async def test_job_kill_joins_kernel_child_and_denies_same_session_sibling(calls, monkeypatch):
    import agentevolver.runtime as runtime_module
    from agentevolver.runtime.kernel import Kernel
    from agentevolver.environment.default.job.environment import JobEnvironment
    class Actor:
        async def __call__(self, **kwargs):
            await asyncio.Event().wait()
    kernel = Kernel()
    monkeypatch.setattr(runtime_module, "kernel", kernel)
    _, manager = calls
    mount(manager, JobEnvironment())
    try:
        parent = await kernel.spawn(Actor(), ctx=ctx("parent"))
        sibling = await kernel.spawn(Actor(), ctx=ctx("sibling"))
        child = await kernel.spawn(Actor(), parent=parent, ctx=ctx("child"))
        denied = await manager("job", "kill", {"job_id": sibling.pid}, ctx(parent.pid))
        assert not denied.success and sibling.alive
        result = await asyncio.wait_for(manager("job", "kill", {"job_id": child.pid}, ctx(parent.pid)), 2)
        assert result.success and child.exited and sibling.alive
    finally:
        await kernel.shutdown(timeout=5)


@pytest.mark.asyncio
async def test_capacity_exempt_controls_work_while_workers_are_full(calls):
    entered, finish, control_entered, control_finish = (asyncio.Event() for _ in range(4))
    class Controlled(Environment):
        name: str = "controlled"
        description: str = "Saturation fixture"
        metadata: dict = Field(default_factory=dict)
        concurrent: bool = True
        max_concurrency: int = 1
        @environment_manager.action(name="evaluate", parallel_safe=True, read_only=True)
        async def evaluate(self, **kwargs):
            entered.set()
            await finish.wait()
            return {"success": True, "message": "computed"}
        @environment_manager.action(name="status", capacity_exempt=True, read_only=True)
        async def status(self, **kwargs):
            control_entered.set()
            await control_finish.wait()
            return {"success": True, "message": "observed"}
    runtime, manager = calls
    mount(manager, Controlled())
    # An active control call neither consumes a worker slot nor changes its capacity.
    control = asyncio.create_task(manager("controlled", "status", {}, ctx("a")))
    await control_entered.wait()
    work = asyncio.create_task(manager("controlled", "evaluate", {}, ctx("a")))
    await asyncio.wait_for(entered.wait(), 1)
    control_finish.set()
    assert (await control).success
    try:
        result = await asyncio.wait_for(manager("controlled", "status", {}, ctx("a")), 1)
        assert result.success and not work.done()
        with pytest.raises(ValueError, match="same capacity"):
            await runtime.invoke("environment", "mismatched", lambda: asyncio.sleep(0),
                ctx=ctx("a"), max_concurrency=2, limit_key=("environment", "controlled"))
    finally:
        finish.set()
        await work


@pytest.mark.asyncio
async def test_registered_background_workers_survive_submission_and_stop_per_job(calls):
    from agentevolver.environment.default.job.environment import JobEnvironment
    from agentevolver.job import job_manager
    runtime, manager = calls
    mount(manager, JobEnvironment())
    started = []
    async def submit():
        jobs = []
        for i in range(2):
            entered = asyncio.Event()
            async def compute(entered=entered):
                entered.set()
                await asyncio.Event().wait()
            task = asyncio.create_task(runtime.invoke("environment", f"trial-{i}", compute,
                ctx=ctx("a"), owner_scoped=True, max_concurrency=2,
                limit_key=("environment", "study-workers")))
            jobs.append(job_manager.register(type="evaluation", label=f"trial-{i}",
                session_id="concurrency-study", handle=task))
            started.append(entered)
        await asyncio.gather(*(event.wait() for event in started))
        return jobs
    jobs = await runtime.invoke("environment", "submit", submit, ctx=ctx("a"))
    assert all(not job.handle.done() for job in jobs)
    stopped = await manager("job", "kill", {"job_id": jobs[0].id}, ctx("a"))
    assert stopped.success and jobs[0].handle.done() and not jobs[1].handle.done()
    await runtime.release(owner="a")
    assert jobs[1].handle.done() and all(job_manager.get(job.id) is None for job in jobs)


@pytest.mark.asyncio
async def test_slow_process_stop_does_not_block_other_owners(calls, monkeypatch):
    import threading
    from agentevolver.environment.default.job.environment import JobEnvironment
    from agentevolver.job import job_manager
    entered, finish = threading.Event(), threading.Event()
    def stop_process(*args):
        entered.set()
        assert finish.wait(3)
    monkeypatch.setattr(job_manager, "_stop_process", stop_process)
    runtime, manager = calls
    mount(manager, JobEnvironment())
    async def launch():
        return job_manager.register(type="test", label="blocking process wait fixture",
            session_id="concurrency-study", handle=SimpleNamespace(pid=123))
    job = await runtime.invoke("tool", "launch", launch, ctx=ctx("a"))
    stopping = asyncio.create_task(manager("job", "kill", {"job_id": job.id}, ctx("a")))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set()
        result = await asyncio.wait_for(manager("job", "list", {}, ctx("b")), .5)
        assert result.success and not stopping.done()
    finally:
        finish.set()
        assert (await stopping).success


@pytest.mark.asyncio
async def test_cancelled_blocking_backend_call_joins_its_thread():
    import threading
    from agentevolver.runtime.invocation import run_blocking
    entered, finish, exited = (threading.Event() for _ in range(3))
    def blocking():
        entered.set()
        assert finish.wait(3)
        exited.set()
    task = asyncio.create_task(run_blocking(blocking))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set()
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and not exited.is_set()
    finally:
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert exited.is_set()
