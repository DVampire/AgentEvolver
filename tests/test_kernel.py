"""The process kernel: states, the two channels, and the two modes.

An agent is a process and the kernel owns its life. What is worth pinning here is not
that a turn runs — that is the loop's test — but the properties the previous runtime got
wrong: that IDLE is a state distinct from EXITED, that a stop is honoured only at a safe
point, that every ending goes through one exit path, and that dispatch and subscription
are the same mechanism.
"""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agentevolver.runtime.envelopes import ReportEnvelope, TaskEnvelope
from agentevolver.runtime.errors import InvalidTransition
from agentevolver.runtime.kernel import Kernel
from agentevolver.runtime.states import ExitStatus, ProcessState, check_transition


class Steps:
    """A process with no model: N awaited steps, each through the safe point."""

    def __init__(self, name="steps", steps=3, sleep=0.01):
        self.name = name
        self.steps = steps
        self.sleep = sleep
        self.log: list[str] = []
        self.events: list = []

    async def __call__(self, task, files=None, ctx=None, **kwargs):
        for index in range(self.steps):
            await self.proc.gate()
            await asyncio.sleep(self.sleep)
            self.log.append(f"step{index}")
        return f"{self.name}:{task}"

    async def on_event(self, envelope, proc):
        self.events.append(envelope)

    async def on_start(self, task, proc):
        self.log.append("on_start")

    async def on_land(self, reason):
        self.log.append(f"on_land:{reason}")

    async def on_exit(self, status):
        self.log.append(f"on_exit:{status.value}")

    async def on_suspend(self):
        self.log.append("on_suspend")

    async def on_resume(self):
        self.log.append("on_resume")


@pytest_asyncio.fixture
async def kernel():
    instance = Kernel()
    try:
        yield instance
    finally:
        await instance.shutdown(timeout=5)


@pytest.mark.asyncio
async def test_worktree_is_retained_when_patch_cannot_be_saved(kernel, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from agentevolver.utils import file_utils

    tree = SimpleNamespace(
        worktree_root=tmp_path / "private", path=tmp_path / "private",
        patch_path=tmp_path / "changes.patch",
        collect_patch=AsyncMock(return_value="complete patch"), cleanup=AsyncMock(),
    )
    tree.worktree_root.mkdir()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(file_utils, "atomic_write_text", fail)
    proc = await kernel.spawn(Steps(), "go", worktree=tree)
    await kernel.wait(proc)
    tree.cleanup.assert_not_awaited()
    assert tree.worktree_root.exists()
    assert proc.artifacts["worktree"] == str(tree.worktree_root)
    assert any("disk full" in str(error) for error in proc.cleanup_errors)


@pytest.mark.asyncio
async def test_spawn_failure_releases_session_lease(kernel, tmp_path, monkeypatch):
    from agentevolver.paths import path_manager

    path_manager.bind_session(owner="spawn-test", session_id="first")

    def fail(*args, **kwargs):
        raise RuntimeError("cannot schedule driver")

    with monkeypatch.context() as patch:
        patch.setattr(asyncio, "create_task", fail)
        with pytest.raises(RuntimeError, match="cannot schedule"):
            await kernel.spawn(Steps(), "go")
    assert not kernel._procs
    path_manager.unbind_session()


@pytest.mark.asyncio
async def test_mailbox_receipt_distinguishes_queued_and_delivered(kernel):
    from agentevolver.runtime.envelopes import ReplyEnvelope
    proc = await kernel.spawn(Steps(steps=100), "work")
    envelope = ReplyEnvelope(text="Change direction")
    assert await kernel.send(proc, envelope)
    assert proc.deliveries[envelope.id]["status"] == "queued"
    assert await kernel.send(proc, envelope)  # a retransmit is not a second event
    assert len(proc.mailbox) == 1
    await asyncio.wait_for(proc.gate(), 1)
    assert proc.deliveries[envelope.id]["status"] == "delivered"
    assert proc.agent.events.count(envelope) == 1
    assert proc.snapshot()["deliveries"][envelope.id]["status"] == "delivered"


@pytest.mark.asyncio
async def test_failed_result_is_failed_exit_and_report(kernel):
    class Failed(Steps):
        async def __call__(self, **kwargs):
            return SimpleNamespace(success=False, message="Token limit reached")

    parent = await kernel.spawn(Steps(), resident=True, start_idle=True)
    await kernel.suspend(parent)
    child = await kernel.spawn(Failed(), "go", parent=parent)
    result = await kernel.wait(child)
    assert not result.success and child.exit_status is ExitStatus.FAILED
    assert not child.turn_success[1]
    report = parent.mailbox.take()
    assert report.exit_status == "failed"


@pytest.mark.asyncio
async def test_parent_reply_matches_active_question(kernel):
    from agentevolver.runtime.envelopes import ReplyEnvelope

    parent = await kernel.spawn(Steps(), resident=True, start_idle=True)
    # Use a process with no mailbox consumer while testing the question protocol.
    from agentevolver.runtime.process import Process
    child = Process("question-child", Steps(), kernel=kernel, parent_pid=parent.pid)
    child.transition(ProcessState.RUNNING)
    kernel._procs[child.pid] = child
    waiting = asyncio.create_task(child.ask_parent("current?", timeout=1))
    await asyncio.sleep(0)
    question = child.waiting_for
    assert question
    assert not await kernel.reply(child, "stale", in_reply_to="old-id")
    await kernel.send(child, ReplyEnvelope(sender="stranger", in_reply_to=question, text="wrong"))
    await kernel.send(child, ReplyEnvelope(sender=parent.pid, in_reply_to="old-id", text="old"))
    assert await kernel.reply(child, "correct", in_reply_to=question)
    assert await waiting == "correct"
    assert not child.waiting_for
    assert not await kernel.reply(child, "too late")


@pytest.mark.asyncio
async def test_unrelated_messages_do_not_reset_reply_timeout(kernel):
    from agentevolver.runtime.process import Process
    parent = await kernel.spawn(Steps(), resident=True, start_idle=True)
    child = Process("timed-child", Steps(), kernel=kernel, parent_pid=parent.pid)
    child.transition(ProcessState.RUNNING)
    kernel._procs[child.pid] = child

    async def noise():
        for _ in range(20):
            await asyncio.sleep(.01)
            child.mailbox.put(TaskEnvelope(task="unrelated"))

    producer = asyncio.create_task(noise())
    try:
        result = await asyncio.wait_for(child.ask_parent("?", timeout=.04), timeout=.15)
        assert result is None and not child.waiting_for
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


@pytest.mark.asyncio
async def test_stopped_process_marks_queued_messages_undelivered(kernel):
    from agentevolver.runtime.envelopes import ReplyEnvelope
    proc = await kernel.spawn(Steps(steps=100), "work")
    await kernel.suspend(proc)
    await asyncio.sleep(0.03)
    envelope = ReplyEnvelope(text="new direction")
    await kernel.send(proc, envelope)
    await kernel.stop(proc, force=True)
    await kernel.wait(proc, timeout=1)
    assert proc.deliveries[envelope.id]["status"] == "undelivered"


@pytest.mark.asyncio
async def test_failed_message_handler_does_not_claim_delivery(kernel):
    from agentevolver.runtime.envelopes import ReplyEnvelope
    class Broken(Steps):
        async def on_event(self, envelope, proc):
            raise ValueError("bad handler")
    proc = await kernel.spawn(Broken(steps=100), "work")
    envelope = ReplyEnvelope(text="change")
    await kernel.send(proc, envelope)
    await proc.gate()
    assert proc.deliveries[envelope.id]["status"] == "failed"
    assert not await kernel.send(proc, envelope)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


def test_the_transition_table_refuses_a_move_it_does_not_have():
    """A bad move is an error, not a state silently coerced into something legal."""
    check_transition(ProcessState.RUNNING, ProcessState.IDLE)
    with pytest.raises(InvalidTransition):
        check_transition(ProcessState.EXITED, ProcessState.RUNNING)
    with pytest.raises(InvalidTransition):
        check_transition(ProcessState.NEW, ProcessState.EXITED)


@pytest.mark.asyncio
async def test_a_one_shot_process_runs_once_and_exits_done(kernel):
    agent = Steps(steps=2)
    proc = await kernel.spawn(agent, "hello")
    assert await kernel.wait(proc, timeout=5) == "steps:hello"
    assert proc.exit_status is ExitStatus.DONE
    assert proc.turns == 1
    assert proc.state is ProcessState.EXITED


@pytest.mark.asyncio
async def test_an_agent_fault_ends_the_process_and_not_the_kernel(kernel):
    class Boom(Steps):
        async def __call__(self, task, files=None, ctx=None, **kwargs):
            raise ValueError("nope")

    proc = await kernel.spawn(Boom(), "x")
    await kernel.wait(proc, timeout=5)
    assert proc.exit_status is ExitStatus.FAILED
    assert "nope" in proc.error
    # The table still answers, which is the point of catching it here.
    assert kernel.list(alive_only=False)


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_holds_at_a_step_boundary_and_resume_continues(kernel):
    agent = Steps(steps=8, sleep=0.05)
    proc = await kernel.spawn(agent, "long")
    await asyncio.sleep(0.06)
    assert await kernel.suspend(proc)
    await asyncio.sleep(0.12)

    assert proc.state is ProcessState.SUSPENDED
    assert "on_suspend" in agent.log
    held = len([line for line in agent.log if line.startswith("step")])
    await asyncio.sleep(0.12)
    assert len([line for line in agent.log if line.startswith("step")]) == held

    assert await kernel.resume(proc)
    await kernel.wait(proc, timeout=5)
    assert "on_resume" in agent.log
    assert proc.exit_status is ExitStatus.DONE


@pytest.mark.asyncio
async def test_a_graceful_stop_lands_and_a_forced_one_does_not(kernel):
    graceful = Steps(name="graceful", steps=50, sleep=0.02)
    gp = await kernel.spawn(graceful, "x")
    await asyncio.sleep(0.05)
    await kernel.stop(gp)
    await kernel.wait(gp, timeout=5)

    forced = Steps(name="forced", steps=50, sleep=0.02)
    fp = await kernel.spawn(forced, "x")
    await asyncio.sleep(0.05)
    await kernel.stop(fp, force=True, reason="no time")
    await kernel.wait(fp, timeout=5)

    assert any(line.startswith("on_land") for line in graceful.log)
    assert not any(line.startswith("on_land") for line in forced.log)
    # Both still exit, and both still run on_exit: one path out, always.
    assert gp.exit_status is fp.exit_status is ExitStatus.CANCELLED
    assert any(line.startswith("on_exit") for line in forced.log)


@pytest.mark.asyncio
async def test_a_stronger_signal_replaces_a_weaker_one(kernel):
    agent = Steps(steps=50, sleep=0.02)
    proc = await kernel.spawn(agent, "x")
    await asyncio.sleep(0.03)
    await kernel.suspend(proc)
    await kernel.stop(proc, reason="stop wins")
    await kernel.wait(proc, timeout=5)
    assert proc.exit_status is ExitStatus.CANCELLED


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_parent_collects_children_from_its_mailbox_without_polling(kernel):
    """The kernel posts a final report to the parent, so waiting is `recv`, not a loop."""

    class Parent(Steps):
        def __init__(self, count):
            super().__init__(name="parent", steps=0)
            self.count = count
            self.reports: list[ReportEnvelope] = []

        async def __call__(self, task, files=None, ctx=None, **kwargs):
            children = [
                await kernel.spawn(Steps(name=f"kid{i}", steps=1), f"sub {i}",
                                   parent=self.proc)
                for i in range(self.count)
            ]
            pending = {child.pid for child in children}
            while pending:
                envelope = await self.proc.recv(timeout=5)
                assert envelope is not None
                if isinstance(envelope, ReportEnvelope) and envelope.final:
                    self.reports.append(envelope)
                    pending.discard(envelope.sender)
            return len(self.reports)

    agent = Parent(3)
    proc = await kernel.spawn(agent, "orchestrate")
    assert await kernel.wait(proc, timeout=10) == 3
    assert all(report.exit_status == "done" for report in agent.reports)


@pytest.mark.asyncio
async def test_escalation_is_a_report_and_a_reply_and_nothing_else(kernel):
    class Blocked(Steps):
        async def __call__(self, task, files=None, ctx=None, **kwargs):
            return await self.proc.ask_parent("which option?", timeout=5)

    class Boss(Steps):
        async def __call__(self, task, files=None, ctx=None, **kwargs):
            child = await kernel.spawn(Blocked(name="blocked"), "go", parent=self.proc)
            while True:
                envelope = await self.proc.recv(timeout=5)
                assert envelope is not None
                if isinstance(envelope, ReportEnvelope) and envelope.blocked:
                    await kernel.reply(envelope.sender, "option B")
                elif isinstance(envelope, ReportEnvelope) and envelope.final:
                    return child.last_result

    proc = await kernel.spawn(Boss(name="boss"), "run")
    assert await kernel.wait(proc, timeout=10) == "option B"


@pytest.mark.asyncio
async def test_a_parent_that_exits_reaps_what_it_dispatched(kernel):
    class Abandoner(Steps):
        def __init__(self):
            super().__init__(name="abandoner", steps=0)
            self.child = None

        async def __call__(self, task, files=None, ctx=None, **kwargs):
            self.child = await kernel.spawn(
                Steps(name="orphan", steps=100, sleep=0.05), "forever", parent=self.proc
            )
            return "left"

    agent = Abandoner()
    proc = await kernel.spawn(agent, "x")
    await kernel.wait(proc, timeout=5)
    await asyncio.sleep(0.1)
    assert agent.child.exited
    assert agent.child.exit_status is ExitStatus.CANCELLED


@pytest.mark.asyncio
async def test_parent_wait_includes_child_cleanup(kernel):
    ready, finish, cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Child(Steps):
        async def __call__(self, **kwargs):
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                await cleanup.wait()

    class Parent(Steps):
        async def __call__(self, **kwargs):
            self.child = await kernel.spawn(Child(), "work", parent=self.proc)
            await ready.wait()
            await finish.wait()

    actor = Parent()
    parent = await kernel.spawn(actor, "work")
    await ready.wait()
    finish.set()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await kernel.wait(parent, timeout=.03)
        assert parent.state is ProcessState.STOPPING
        assert kernel.forget() == 0
    finally:
        cleanup.set()
    await kernel.wait(parent, timeout=1)
    assert actor.child._exited.is_set()


@pytest.mark.asyncio
async def test_shutdown_retains_unfinished_cleanup(kernel):
    ready, cleanup = asyncio.Event(), asyncio.Event()

    class SlowExit(Steps):
        async def __call__(self, **kwargs):
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                await cleanup.wait()

    proc = await kernel.spawn(SlowExit(), "work")
    await ready.wait()
    try:
        assert await kernel.shutdown(timeout=.01) == [proc.pid]
        assert kernel.get(proc.pid) is proc
        assert not proc._exited.is_set()
        with pytest.raises(RuntimeError, match="shutting down"):
            await kernel.spawn(Steps(), "new work")
    finally:
        cleanup.set()
    await kernel.wait(proc, timeout=1)
    assert await kernel.shutdown(timeout=1) == []


@pytest.mark.asyncio
async def test_stop_before_driver_starts_and_repeated_stop(kernel):
    actor = Steps()
    proc = await kernel.spawn(actor, "must not run")
    await kernel.stop(proc, force=True)
    await kernel.stop(proc, force=True)
    await kernel.wait(proc, timeout=1)
    assert not any(item.startswith("step") for item in actor.log)
    assert proc.exit_status is ExitStatus.CANCELLED


@pytest.mark.asyncio
async def test_descendants_inherit_permission_ceiling(kernel):
    from agentevolver.permission import permission_manager, PermissionRequest, Operation

    class Writer(Steps):
        permission_mode = "danger_full_access"
        async def __call__(self, **kwargs):
            return permission_manager.check_declared(
                "writer", PermissionRequest(op=Operation.WRITE, target="/tmp/audit-no-write"),
                mode=self.permission_mode,
            ).allowed

    parent_actor = Steps()
    parent_actor.permission_mode = "read_only"
    parent = await kernel.spawn(parent_actor, resident=True, start_idle=True)
    child = await kernel.spawn(Writer(), "check only", parent=parent)
    assert child.permission_mode == "read_only"
    assert await kernel.wait(child, timeout=1) is False


@pytest.mark.asyncio
async def test_run_budget_survives_turns_and_child_collection(kernel):
    from agentevolver.runtime.errors import BudgetExhausted
    from agentevolver.model.types import TokenUsage

    actor = Steps()
    actor.max_token = 10
    parent = await kernel.spawn(actor, resident=True, start_idle=True)
    child = await kernel.spawn(Steps(), "work", parent=parent)
    assert child.budget is parent.budget
    child.budget.record(TokenUsage(input_tokens=2, cache_read_tokens=3, output_tokens=1))
    await kernel.wait(child, timeout=1)
    kernel.forget()
    assert parent.budget.tokens == 6
    parent.budget.record(TokenUsage(input_tokens=4, cost=.1, cost_status="reported"))
    with pytest.raises(BudgetExhausted, match="Run token"):
        await kernel.spawn(Steps(), "over budget", parent=parent)
    assert parent.snapshot()["run_budget"]["reported_cost"] == .1
    assert parent.budget.unknown_cost_calls == 1


@pytest.mark.asyncio
async def test_cleanup_failure_is_visible_without_skipping_later_cleanup(kernel):
    class BrokenCleanup(Steps):
        async def on_land(self, reason):
            raise RuntimeError("could not persist landing")

        async def on_exit(self, status):
            self.log.append("exit attempted")
            raise RuntimeError("could not close external job")

    actor = BrokenCleanup()
    proc = await kernel.spawn(actor, "work")
    await kernel.wait(proc, timeout=1)
    assert "exit attempted" in actor.log
    assert proc._exited.is_set()
    errors = proc.snapshot()["cleanup_errors"]
    assert [item["phase"] for item in errors] == ["on_land", "on_exit"]
    assert "external job" in errors[1]["error"]


def test_run_budget_resume_keeps_usage_and_rejects_corruption(tmp_path):
    import json
    from dataclasses import asdict
    from agentevolver.runtime.process import RunBudget
    from agentevolver.runtime.errors import BudgetExhausted
    from agentevolver.model.types import TokenUsage

    path = tmp_path / "budget.json"
    first = RunBudget(limit=10)
    first.bind(path, resume=False)
    first.record(TokenUsage(input_tokens=7, output_tokens=3))
    resumed = RunBudget(limit=10)
    resumed.bind(path, resume=True)
    assert resumed.tokens == 10
    with pytest.raises(BudgetExhausted):
        resumed.check()
    with pytest.raises(ValueError, match="already exists"):
        RunBudget(limit=10).bind(path, resume=False)
    path.write_text('{"version":1,"budget":{"tokens":-1}}')
    with pytest.raises(ValueError, match="Invalid persisted"):
        RunBudget(limit=10).bind(path, resume=True)
    for field, value in (("tokens", -1), ("tokens", 0), ("estimated_cost", float("nan"))):
        path.write_text(json.dumps({"version": 1, "budget": {**asdict(first), field: value}}))
        untouched = RunBudget(limit=10)
        with pytest.raises(ValueError, match="Invalid persisted"):
            untouched.bind(path, resume=True)
        assert untouched.tokens == 0


def test_inflight_budget_is_reserved_and_usage_is_not_counted_twice(tmp_path):
    from agentevolver.runtime.process import RunBudget
    from agentevolver.runtime.errors import BudgetExhausted
    from agentevolver.model.types import TokenUsage

    ledger = RunBudget(limit=100)
    ledger.bind(tmp_path / "budget.json", resume=False)
    with ledger.request("first", 70) as settle:
        assert ledger.reserved == 70
        with pytest.raises(BudgetExhausted):
            with ledger.request("second", 40):
                pytest.fail("request should not start")
        usage = settle({"input_tokens": 10, "output_tokens": 5})
    ledger.record(TokenUsage.from_raw(usage))
    assert ledger.tokens == 15 and ledger.reserved == 0


def test_unknown_usage_survives_resume_until_explicit_reconciliation(tmp_path):
    from agentevolver.runtime.process import RunBudget
    from agentevolver.runtime.errors import BudgetExhausted

    path = tmp_path / "budget.json"
    ledger = RunBudget(limit=100)
    ledger.bind(path, resume=False)
    with pytest.raises(TimeoutError):
        with ledger.request("timeout-route", 70):
            raise TimeoutError("provider may have accepted")
    resumed = RunBudget(limit=100)
    resumed.bind(path, resume=True)
    with pytest.raises(BudgetExhausted, match="unreconciled"):
        resumed.check()
    key = next(iter(resumed.requests))
    usage = {"input_tokens": 20, "output_tokens": 10}
    resumed.reconcile(key, usage, evidence="provider request audit 123")
    resumed.reconcile(key, usage, evidence="same audit")
    resumed.check()
    assert resumed.tokens == 30 and resumed.reserved == 0
    with pytest.raises(ValueError, match="Conflicting"):
        resumed.reconcile(key, {"input_tokens": 99}, evidence="different claim")


def test_budget_scope_is_inherited_without_leaking():
    from agentevolver.runtime.process import RunBudget
    outer, inner = RunBudget(), RunBudget()
    assert RunBudget.current() is None
    with outer.scope():
        assert RunBudget.current() is outer
        with inner.scope():
            assert RunBudget.current() is inner
        assert RunBudget.current() is outer
    assert RunBudget.current() is None


def test_cost_only_response_does_not_settle_token_reservation():
    from agentevolver.runtime.process import RunBudget
    from agentevolver.runtime.errors import BudgetExhausted

    ledger = RunBudget()
    with ledger.request("missing-tokens", 100) as settle:
        settle({"cost": 0.5})
    assert ledger.reserved == 100 and ledger.tokens == 0
    with pytest.raises(BudgetExhausted, match="unreconciled"):
        ledger.check()


def test_run_budget_preserves_explicit_context_total(tmp_path):
    from agentevolver.runtime.process import RunBudget
    from agentevolver.model.types import TokenUsage

    path = tmp_path / "usage.json"
    ledger = RunBudget(limit=100)
    ledger.bind(path, resume=False)
    ledger.record(TokenUsage(context_input_tokens=20, input_tokens=3, output_tokens=2))
    restored = RunBudget(limit=100)
    restored.bind(path, resume=True)
    assert restored.tokens == 22
    assert restored.context_input_tokens == 20
    with pytest.raises(ValueError, match="Invalid reported"):
        restored.record(TokenUsage(input_tokens=-2))
    assert restored.tokens == 22


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_subscriber_registers_idle_and_spends_one_turn_per_event(kernel):
    agent = Steps(name="watcher", steps=1)
    proc = await kernel.spawn(agent, "watch deployments", topics=["deploy"])
    await asyncio.sleep(0.05)

    # Idle, not finished, and no turn spent waiting for work that has not arrived.
    assert proc.state is ProcessState.IDLE
    assert proc.turns == 0

    assert await kernel.publish("deploy", "started", {"service": "api"}) == 1
    await asyncio.sleep(0.15)
    assert await kernel.publish("deploy", "finished", {"service": "api"}) == 1
    await asyncio.sleep(0.15)

    assert proc.turns == 2
    assert proc.state is ProcessState.IDLE


@pytest.mark.asyncio
async def test_a_topic_edge_dies_with_its_subscriber(kernel):
    agent = Steps(name="watcher", steps=1)
    proc = await kernel.spawn(agent, "brief", topics=["deploy"])
    await asyncio.sleep(0.05)
    await kernel.stop(proc, force=True)
    await kernel.wait(proc, timeout=5)
    assert await kernel.publish("deploy", "started") == 0


@pytest.mark.asyncio
async def test_a_message_to_a_running_process_is_delivered_at_the_safe_point(kernel):
    agent = Steps(steps=4, sleep=0.03)
    proc = await kernel.spawn(agent, "x")
    await kernel.send(proc, TaskEnvelope(task="an aside"))
    await kernel.wait(proc, timeout=5)
    # RUNNING + message → on_event. IDLE + message → the next turn. One rule each.
    assert [type(event).__name__ for event in agent.events] == ["TaskEnvelope"]


# ---------------------------------------------------------------------------
# What observers are told
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def observed(monkeypatch):
    """Capture broadcasts instead of letting them reach the hook registry.

    Via `events.broadcast`, which is the seam every one of these goes through, so a
    firing site that stops calling it fails here rather than going quiet.
    """
    from agentevolver.agent.loop import events as bus

    seen: list = []

    async def broadcast(event, payload=None, *, ctx=None):
        seen.append((event, payload or {}))

    monkeypatch.setattr(bus.events, "broadcast", broadcast)
    return seen


@pytest.mark.asyncio
async def test_a_root_process_opens_a_session_and_a_child_opens_a_subagent(kernel, observed):
    """The two were both SESSION_START, so dispatches were counted as sessions."""
    parent = await kernel.spawn(Steps(name="parent", steps=1), "root task", resident=True)
    child = await kernel.spawn(Steps(name="child", steps=1), "sub task", parent=parent)
    await kernel.wait(child, timeout=5)
    await kernel.stop(parent, force=True)
    await kernel.wait(parent, timeout=5)

    def names(pid):
        return [event.name for event, body in observed if body.get("task_id") == pid]

    assert names(parent.pid) == ["USER_PROMPT_SUBMIT", "SESSION_START",
                                 "TASK_COMPLETED", "SESSION_END"]
    assert names(child.pid) == ["SUBAGENT_START", "TASK_COMPLETED", "SUBAGENT_STOP"]


@pytest.mark.asyncio
async def test_a_suspend_and_a_resume_are_visible_from_outside_the_process(kernel, observed):
    """The kernel had the phase and nothing outside could see it.

    Without this a held process reads in a trace exactly like one that stopped
    producing steps on its own.
    """
    agent = Steps(steps=6, sleep=0.02)
    proc = await kernel.spawn(agent, "x")
    await asyncio.sleep(0.03)
    await kernel.suspend(proc)
    await asyncio.sleep(0.05)
    assert proc.state is ProcessState.SUSPENDED
    await kernel.resume(proc)
    await kernel.wait(proc, timeout=5)

    held = [event.name for event, _ in observed
            if event.name in ("ON_SUSPEND", "ON_RESUME")]
    assert held == ["ON_SUSPEND", "ON_RESUME"]
    # The agent's own phase hooks still ran; the broadcast is additional, not a
    # replacement — one is for the process, the other for everyone watching it.
    assert "on_suspend" in agent.log and "on_resume" in agent.log


@pytest.mark.asyncio
async def test_a_scoped_publish_reaches_processes_that_subscribed_by_name(kernel):
    """Subscribe and publish must scope the topic the same way, or nothing is delivered.

    `spawn(topics=[...])` registered the raw name while `publish_scoped` looked up
    `{root}::{name}`, so the two never matched. It failed silently and in the worst
    possible shape: the fan-out reported success with a count of zero, and every
    resident subscriber sat IDLE forever waiting for an event delivered to nobody.
    Caught on a live run — four subscribers registered and
    `📡 publish … → 0 subscriber(s)`.
    """
    ctx = SimpleNamespace(id="sess-1", extra={"root_session_id": "sess-1"})
    agents = [Steps(name=f"sub{i}", steps=1) for i in range(3)]
    for agent in agents:
        await kernel.spawn(agent, "standing brief", ctx=ctx, topics=["deployment.ready"])
    await asyncio.sleep(0.05)

    delivered, name, _ = await kernel.publish_scoped(
        "deployment.ready", "deployment.ready", {"url": "http://site"}, ctx=ctx,
    )
    assert name == "sess-1::deployment.ready"
    assert delivered == 3, "a scoped publish must reach every process that subscribed"


@pytest.mark.asyncio
async def test_a_topic_still_pairs_up_without_a_session(kernel):
    """A bare kernel — a test, a script — keeps the plain name on both sides."""
    agent = Steps(name="watcher", steps=1)
    await kernel.spawn(agent, "brief", topics=["plain"])
    await asyncio.sleep(0.05)
    assert await kernel.publish("plain", "started") == 1


@pytest.mark.asyncio
async def test_a_resident_keeps_its_standing_brief_however_it_is_woken(kernel):
    """A subscriber's brief is its identity, and every turn needs it.

    `_input_text` returned `envelope.task` alone for a TaskEnvelope, so a resident woken
    by `send_message` — as opposed to by a published event — lost the standing brief it
    was spawned with. In the website demo that brief carries the participant's assigned
    persona, and the subscriber answered "NO ASSIGNED CONTEXT": a failure that reads like
    the parent forgot to assign one rather than like the kernel discarding it.
    """
    seen: list[str] = []

    class Panelist(Steps):
        async def __call__(self, task, files=None, ctx=None, **kwargs):
            seen.append(task)
            await self.proc.gate()
            return "ok"

    ctx = SimpleNamespace(id="sess-1", extra={"root_session_id": "sess-1"})
    proc = await kernel.spawn(
        Panelist(name="panelist"), "You are participant_01. Report as that resident.",
        ctx=ctx, topics=["deployment.ready"],
    )
    await asyncio.sleep(0.05)

    await kernel.publish_scoped(
        "deployment.ready", "deployment.ready", {"url": "http://site"}, ctx=ctx
    )
    await asyncio.sleep(0.15)
    await kernel.send(proc, TaskEnvelope(task="Release 2 is up; take another look."))
    await asyncio.sleep(0.15)

    assert len(seen) == 2, seen
    for turn in seen:
        assert "participant_01" in turn, f"the brief was dropped: {turn!r}"
    assert "http://site" in seen[0]
    assert "Release 2" in seen[1]


@pytest.mark.asyncio
async def test_a_one_shot_dispatch_is_not_prefixed_with_anything(kernel):
    """Only a resident has a brief; a plain dispatch must read exactly as it was sent."""
    agent = Steps(name="worker", steps=1)
    proc = await kernel.spawn(agent, "do exactly this")
    assert await kernel.wait(proc, timeout=5) == "worker:do exactly this"
def test_durable_mailbox_recovers_queue_topics_and_deduplicates(tmp_path):
    from agentevolver.runtime.mailbox import Mailbox
    from agentevolver.runtime.envelopes import TaskEnvelope

    path = tmp_path / "endpoint.json"
    identity = {"thread": "person-1"}
    first = Mailbox()
    first.bind(path, identity=identity, topics=["root::release"])
    message = TaskEnvelope(task="complete input" * 10000)
    first.put(message)
    first.subscribe("root::feedback")
    first.subscribe("root::release", remove=True)
    # A real crash releases the OS lock without closing/draining the inbox.
    first.release()
    second = Mailbox()
    second.bind(path, identity=identity, resume=True)
    assert second.topics == ("root::feedback",)
    assert second.take() == message
    second.receipt(message, "delivered")
    second.put(message)
    assert second.take() is None
    second.release()


def test_uncertain_mailbox_requires_reconciliation_and_exclusive_ownership(tmp_path):
    import pytest
    from agentevolver.runtime.mailbox import Mailbox
    from agentevolver.runtime.envelopes import TaskEnvelope

    path = tmp_path / "endpoint.json"
    first = Mailbox()
    first.bind(path, identity={})
    message = TaskEnvelope(task="deploy")
    first.put(message)
    assert first.take() == message
    with pytest.raises(BlockingIOError):
        Mailbox().bind(path, identity={}, resume=True)
    with pytest.raises(BlockingIOError):
        Mailbox.reconcile(path, message.id, replay=False, evidence="checked")
    first.release()
    with pytest.raises(RuntimeError, match="Uncertain delivery"):
        Mailbox().bind(path, identity={}, resume=True)
    Mailbox.reconcile(path, message.id, replay=False, evidence="Deployment registry confirms completion")
    resumed = Mailbox()
    resumed.bind(path, identity={}, resume=True)
    assert resumed.take() is None
    resumed.release()


def test_mailbox_does_not_enqueue_on_persistence_failure(tmp_path, monkeypatch):
    import pytest
    from agentevolver.runtime.mailbox import Mailbox
    from agentevolver.runtime.envelopes import TaskEnvelope
    from agentevolver.utils import file_utils

    inbox = Mailbox()
    inbox.bind(tmp_path / "inbox.json", identity={})

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(file_utils, "atomic_write_text", fail)
    with pytest.raises(OSError):
        inbox.put(TaskEnvelope(task="must not run"))
    assert len(inbox) == 0
    inbox.release()


def test_mailbox_turn_completion_is_atomic_on_disk_full(tmp_path, monkeypatch):
    from agentevolver.runtime.mailbox import Mailbox
    from agentevolver.utils import file_utils

    path = tmp_path / "inbox.json"
    inbox = Mailbox()
    inbox.bind(path, identity={})
    envelope = TaskEnvelope(task="make a release")
    inbox.put(envelope)
    inbox.take()
    before = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(file_utils, "atomic_write_text", fail)
    with pytest.raises(OSError, match="disk full"):
        inbox.receipt(envelope, "delivered", turn=(1, "finished", True))
    assert path.read_bytes() == before
    assert inbox.delivered(envelope.id) == "received"
    assert inbox.turns == {}
    inbox.release()


@pytest.mark.asyncio
async def test_kernel_recovers_turns_queue_and_subscriptions_after_process_crash(tmp_path, monkeypatch):
    import os
    import sys
    import textwrap
    from agentevolver.paths import path_manager

    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    # os._exit bypasses Python/asyncio cleanup and leaves a real queued envelope.
    script = textwrap.dedent('''
        import asyncio, os
        from types import SimpleNamespace
        from agentevolver.paths import path_manager
        from agentevolver.runtime.kernel import Kernel
        from agentevolver.runtime.envelopes import TaskEnvelope
        class Agent:
            name = "recoverable"
            async def __call__(self, task, **kwargs):
                return "result:" + task
        async def main():
            path_manager.bind_session("recovery-test", "root")
            kernel = Kernel()
            ctx = SimpleNamespace(id="resident", extra={"root_session_id": "root"})
            proc = await kernel.spawn(Agent(), "standing brief", ctx=ctx,
                                      topics=["releases"], thread_id="resident")
            await kernel.send(proc, TaskEnvelope(task="first", id="first", at=1))
            while proc.turns < 1:
                await asyncio.sleep(.01)
            await kernel.send(proc, TaskEnvelope(task="second", id="second"))
            os._exit(23)
        asyncio.run(main())
    ''')
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", script,
        env=dict(os.environ), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    output, errors = await asyncio.wait_for(child.communicate(), 30)
    assert child.returncode == 23, (output, errors)
    path_manager.bind_session("recovery-test", "root")
    resumed = Kernel()
    ctx = SimpleNamespace(id="resident", extra={"root_session_id": "root"})
    try:
        proc = await resumed.spawn(Steps(name="recoverable", steps=1), "standing brief", ctx=ctx,
                                   topics=["releases"], thread_id="resident", resume=True)
        assert proc.turns == 1
        assert "first" in proc.turn_results[1]
        async with asyncio.timeout(5):
            while proc.turns < 2:
                await asyncio.sleep(.01)
        assert "second" in proc.turn_results[2]
        with pytest.raises(ValueError, match="different content"):
            await resumed.send(proc, TaskEnvelope(task="altered", id="first", at=1))
        assert await resumed.send(proc, TaskEnvelope(task="first", id="first", at=1))
        assert len(proc.mailbox) == 0
        count, _, _ = await resumed.publish_scoped("releases", "ready", {"release": 3}, ctx=ctx)
        assert count == 1
        async with asyncio.timeout(5):
            while proc.turns < 3:
                await asyncio.sleep(.01)
        assert "ready" in proc.turn_results[3]
    finally:
        await resumed.shutdown(timeout=5)
        path_manager.unbind_session()


@pytest.mark.asyncio
async def test_cancelling_file_lock_wait_releases_local_lock_and_handle(tmp_path):
    import fcntl
    from agentevolver.utils.file_utils import file_lock

    path = tmp_path / "manifest.json"
    with path.with_suffix(".json.lock").open("a+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pending = file_lock(path)
        task = asyncio.create_task(pending.__aenter__())
        await asyncio.sleep(.1)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pending._handle is None
        assert not file_lock.get_lock(path).locked()
        fcntl.flock(owner, fcntl.LOCK_UN)
    async with asyncio.timeout(1):
        async with file_lock(path):
            pass


def test_active_session_refuses_config_replacement_before_reading_or_mutating():
    from argparse import Namespace
    from agentevolver.config import config
    from agentevolver.paths import path_manager

    path_manager.bind_session("config-test", "active")
    before = config.to_dict()
    with path_manager.lease():
        with pytest.raises(RuntimeError, match="active run"):
            config.initialize("does-not-exist.py", Namespace(), verbose=False)
    assert config.to_dict() == before
    path_manager.unbind_session()
