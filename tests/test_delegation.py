"""Delegating work must not cost the parent the whole child's run, and must not lose it.

Dispatching a sub-agent blocked the parent until the child finished, so an orchestrator
spent its own step budget on wall-clock it was not using — the defect `agentevolver/job/`
exists for, one level up. Backgrounding a child introduces three ways to lose the work
instead: two turns delivered to one agent start two runs on the same ref and the first
result vanishes with no error; a child nobody collects is work paid for and thrown away;
and a child nobody reaps goes on calling a model after the run that wanted it has ended.

The file also pins the boundary between the two channels a child and its parent share.
`report`/`send_message` is a mailbox — nothing blocks, everything accumulates where the
parent already collects. `escalate`/`reply` is a rendezvous — the child stops until it is
answered. Collapsing them would either make a report block or make an escalation
collectable-whenever, and both strand a child.
"""

import asyncio
import contextlib
import io
from types import SimpleNamespace

import pytest

from agentevolver.agent.types import _delegation_summary
from agentevolver.job import job_manager
from agentevolver.job.types import JobStatus
from agentevolver.plan.server import action_is_allowed
from agentevolver.response.types import Response, ResponseType
from agentevolver.runtime import runtime_manager
from agentevolver.runtime.types import TaskMessage
from agentevolver.tool.default.report import ReportTool

SESSION = "parent-session"


class _Child:
    """A stand-in for an Agent: answers a TaskMessage the way the runtime pump expects.

    Deliberately not a real Agent. What is under test is the delegation layer — turn
    serialization, the transcript, reaping — and a real agent would drag a model call
    into every one of these assertions.
    """

    name = "stub_agent"

    def __init__(self, delay: float = 0.0, succeed: bool = True):
        self.delay = delay
        self.succeed = succeed
        self.turns = []  # (task, ctx) per turn, in the order they were run
        self.running = 0
        self.overlapped = False  # True if two turns were ever in flight at once

    async def handle(self, msg, ref):
        if not isinstance(msg, TaskMessage):
            return
        self.running += 1
        self.overlapped = self.overlapped or self.running > 1
        self.turns.append((msg.task, msg.kwargs.get("ctx")))
        await asyncio.sleep(self.delay)
        self.running -= 1
        if msg.reply_future is not None and not msg.reply_future.done():
            msg.reply_future.set_result(
                Response(
                    type=ResponseType.AGENT,
                    success=self.succeed,
                    message=f"answer {len(self.turns)}",
                )
            )


class _Parent:
    """The dispatching agent's context: an id to scope the registry by, plus ambient roots."""

    def __init__(self, id=SESSION, *, project_id=""):
        self.id = id
        self.extra = {"workspace_root": "/ws"}
        if project_id:
            self.extra["project_id"] = project_id


@pytest.fixture(autouse=True)
def _reap():
    """Every test leaves the registries empty, whatever it did to them.

    A leaked background child is a live task that keeps answering into the next test's
    assertions, which is the kind of failure that moves when you look at it.
    """
    yield
    for session in (SESSION, "other-session"):
        runtime_manager.forget(session)
        job_manager.forget(session)


async def _start(child, task, **brief):
    """Background one child and hand back the ref the runtime holds for it.

    There is no sub-agent type to return: a delegated child is an ``AgentRef`` like any
    other running agent, and the delegation shows up as fields on it.
    """
    started = await runtime_manager.delegate_background(child, task, **brief)
    assert started.success, started.message
    return runtime_manager.child(started.data["job_id"])


async def _report(job_id: str, output: str):
    """What a child does when it calls `report_tool`, driven through the tool itself.

    Going through the tool rather than re-implementing its one line is what stops these
    assertions from passing while the thing that actually writes the transcript is broken.
    """

    class _Ctx:
        extra = {"report_job_id": job_id, "report_agent_name": ""}

    return await ReportTool()(output=output, ctx=_Ctx())


async def _until(predicate, timeout=5.0):
    """Wait for a condition rather than for a duration.

    Sleeping a fixed interval either flakes under load or makes the suite slow; every
    wait here is on something the code actually did.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


# --------------------------------------------------------------------------- #
# A background child is a job
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_background_child_is_a_job_rather_than_a_second_registry():
    """ "What is running" has to have one answer.

    A separate sub-agent registry would mean `job_list_tool` shows the backgrounded
    shell command and not the backgrounded child — wrong in exactly the moment the
    parent is trying to work out what it is still waiting on — and a second set of
    list/read/stop tools for the same three questions.
    """
    child = _Child(delay=0.05)
    sub = await _start(child, "investigate the parser", parent_ctx=_Parent())

    job = job_manager.get(sub.job_id)
    assert job is not None and job.type == "agent"
    assert job.session_id == SESSION
    assert sub.job_id in [j.id for j in job_manager.list(SESSION)]


@pytest.mark.asyncio
async def test_backgrounding_returns_before_the_child_has_answered():
    """The whole point. If `start` waited for the first turn, the parent would have paid
    for the delegation it was trying not to pay for."""
    child = _Child(delay=0.5)
    sub = await _start(child, "a slow job", parent_ctx=_Parent())

    assert sub.busy
    assert job_manager.get(sub.job_id).status is JobStatus.RUNNING


@pytest.mark.asyncio
async def test_blocking_delegate_is_visible_as_a_live_agent_thread():
    """A child does not disappear from the control surface because its parent awaits it."""
    pending = asyncio.create_task(
        runtime_manager.delegate(
            _Child(delay=0.1), "blocking work", parent_ctx=_Parent(project_id="project-a"),
        )
    )
    assert await _until(lambda: bool(job_manager.list(SESSION)))
    job = job_manager.list(SESSION)[0]
    ref = runtime_manager.child(job.id)

    assert ref is not None
    assert ref.project_id == "project-a"
    assert ref.busy and not ref.continuable

    await pending
    assert not ref.alive


@pytest.mark.asyncio
async def test_what_the_child_returns_is_collectable_afterwards():
    """A background child's answer reaches its parent by being read, not by being pushed.

    Pushing it would insert content between a step's decision and its result, which is
    the one place the parent's history has to stay ordered — the same reason
    `job/README.md` gives for collecting rather than delivering.
    """
    child = _Child()
    sub = await _start(child, "count the tests", parent_ctx=_Parent())

    assert await _until(lambda: job_manager.get(sub.job_id).status.is_final)
    assert "answer 1" in job_manager.output(sub.job_id)


@pytest.mark.asyncio
async def test_a_child_that_ends_without_finishing_is_not_reported_as_success():
    """The parent decides whether to redo the work from this exit code.

    A child that stopped on its step budget returns a Response like any other; reading
    only "it ended" would file an unfinished job under done.
    """
    child = _Child(succeed=False)
    sub = await _start(child, "an impossible job", parent_ctx=_Parent())

    assert await _until(lambda: job_manager.get(sub.job_id).status.is_final)
    assert job_manager.get(sub.job_id).status is JobStatus.FAILED


# --------------------------------------------------------------------------- #
# One-shot and continuable
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_one_shot_child_is_over_once_it_answers():
    """It answers once and ends — so its ref must not be left running.

    A one-shot child whose pump stayed alive would hold its context, its memory and a
    slot in the runtime for the rest of the session, and nothing would ever stop it: the
    job is final, so the reaper walks past it.
    """
    child = _Child()
    sub = await _start(child, "one question", parent_ctx=_Parent())
    ref_name = sub.name

    assert await _until(lambda: not sub.alive)
    assert runtime_manager.get(ref_name) is None


@pytest.mark.asyncio
async def test_a_continuable_child_outlives_its_own_answer():
    """The distinction the whole module exists for.

    Idle is not finished: the child still holds the conversation, so the job stays
    running and the parent can still reach it.
    """
    child = _Child()
    sub = await _start(child, "first task", continuable=True, parent_ctx=_Parent())

    assert await _until(lambda: sub.alive and not sub.busy)
    assert sub.alive
    assert job_manager.get(sub.job_id).status is JobStatus.RUNNING


@pytest.mark.asyncio
async def test_job_wait_collects_a_continuable_turn_without_model_polling():
    from agentevolver.environment.default.job import JobEnvironment

    sub = await _start(_Child(delay=0.05), "first", continuable=True, parent_ctx=_Parent())
    result = await JobEnvironment().wait(
        job_ids=[sub.job_id], condition="idle_after_turn", min_turns=1,
        timeout=1, ctx=_Parent(),
    )

    assert result["success"]
    assert result["jobs"][0] == {
        "job_id": sub.job_id,
        "status": "running",
        "ready": True,
        "turns": 1,
        "alive": True,
        "busy": False,
        "queued": 0,
    }


@pytest.mark.asyncio
async def test_job_wait_returns_early_when_a_subscriber_dies():
    from agentevolver.environment.default.job import JobEnvironment

    sub = await _start(_Child(), "first", continuable=True, parent_ctx=_Parent())
    assert await _until(lambda: sub.turns == 1 and not sub.busy)
    job_manager.kill(sub.job_id)
    assert await _until(lambda: not sub.alive)

    result = await JobEnvironment().wait(
        job_ids=[sub.job_id], condition="idle_after_turn", min_turns=2,
        timeout=1, ctx=_Parent(),
    )
    assert not result["success"]
    assert not result["timed_out"]


@pytest.mark.asyncio
async def test_a_message_becomes_the_continuable_child_s_next_turn():
    child = _Child()
    sub = await _start(child, "first task", continuable=True, parent_ctx=_Parent())
    assert await _until(lambda: sub.alive and not sub.busy)

    await runtime_manager.send_to_child(sub.job_id, "second task", session_id=SESSION)

    assert await _until(lambda: sub.turns == 2)
    assert [task for task, _ in child.turns] == ["first task", "second task"]
    assert "answer 2" in job_manager.output(sub.job_id)


@pytest.mark.asyncio
async def test_descendants_share_the_root_tree_messaging_authority():
    """A grandchild belongs to the same task tree even though its direct parent differs."""
    parent = _Parent(project_id="project-a")
    first = await _start(
        _Child(delay=5.0), "first level", continuable=True, parent_ctx=parent,
    )
    grandchild = await _start(
        _Child(),
        "second level",
        continuable=True,
        parent_ctx=first._ctx,
        parent_ref=first,
    )
    assert grandchild.parent_session_id == first.session_id
    assert grandchild.root_session_id == SESSION
    assert grandchild.project_id == "project-a"

    delivered = await runtime_manager.send_to_child(
        grandchild.job_id, "follow-up", session_id=SESSION,
    )

    assert delivered.success
    drivers = [ref._driver for ref in (first, grandchild) if ref._driver is not None]
    runtime_manager.forget(SESSION)
    await asyncio.gather(*drivers, return_exceptions=True)
    job_manager.forget(first.session_id)


@pytest.mark.asyncio
async def test_a_continuable_child_keeps_one_session_across_turns():
    """Continuing the conversation is the point; a fresh context per turn would make
    `send_message_tool` a slower way to start a new child.

    The session id is what memory, trace and the workspace are keyed by, so it is the
    identity that has to survive — not merely the fact that the same object was reused.
    """
    child = _Child()
    sub = await _start(child, "first", continuable=True, parent_ctx=_Parent())
    assert await _until(lambda: sub.alive and not sub.busy)
    await runtime_manager.send_to_child(sub.job_id, "second", session_id=SESSION)
    assert await _until(lambda: sub.turns == 2)

    first_ctx, second_ctx = child.turns[0][1], child.turns[1][1]
    assert first_ctx.id == second_ctx.id == sub.session_id


@pytest.mark.asyncio
async def test_two_messages_never_put_two_turns_on_one_child_at_once():
    """The reason a driver exists at all.

    Delivering two tasks into an agent's inbox back to back starts a second run on the
    same ref while the first is still going, and `on_start` keys its run by ref name —
    so the second overwrites the first and the first turn's result is lost with nothing
    logged. Queueing makes "your message becomes its next turn" true rather than a hope.
    """
    child = _Child(delay=0.05)
    sub = await _start(child, "first", continuable=True, parent_ctx=_Parent())

    await runtime_manager.send_to_child(sub.job_id, "second", session_id=SESSION)
    await runtime_manager.send_to_child(sub.job_id, "third", session_id=SESSION)

    assert await _until(lambda: sub.turns == 3)
    assert not child.overlapped, "two turns ran on one child at the same time"
    transcript = job_manager.output(sub.job_id)
    assert all(f"answer {n}" in transcript for n in (1, 2, 3)), "a turn's result was lost"


@pytest.mark.asyncio
async def test_a_message_to_a_working_child_says_it_will_wait():
    """The parent has to know a message cannot redirect work already underway.

    Told otherwise, an orchestrator sends a correction and reads the turn already in
    flight as the answer to it.
    """
    child = _Child(delay=0.3)
    sub = await _start(child, "first", continuable=True, parent_ctx=_Parent())

    sent = await runtime_manager.send_to_child(sub.job_id, "second", session_id=SESSION)
    assert sent.success and "waits its turn" in sent.message


@pytest.mark.asyncio
async def test_a_one_shot_child_refuses_a_message_rather_than_swallowing_it():
    """Accepting a message no child will ever read is the worst outcome available: the
    parent waits for a turn that was never queued, on a child that has already ended."""
    child = _Child()
    sub = await _start(child, "one question", parent_ctx=_Parent())
    assert await _until(lambda: not sub.alive)

    sent = await runtime_manager.send_to_child(sub.job_id, "more work", session_id=SESSION)
    assert not sent.success and "one-shot" in sent.message


@pytest.mark.asyncio
async def test_a_message_to_a_stopped_child_is_a_failure_not_a_silence():
    """A continuable child can still be gone — killed, or reaped with its run."""
    child = _Child()
    sub = await _start(child, "first", continuable=True, parent_ctx=_Parent())
    assert await _until(lambda: sub.alive and not sub.busy)
    job_manager.kill(sub.job_id)
    assert await _until(lambda: not sub.alive)

    sent = await runtime_manager.send_to_child(sub.job_id, "more work", session_id=SESSION)
    assert not sent.success and "already ended" in sent.message


@pytest.mark.asyncio
async def test_one_session_cannot_send_work_into_another_session_s_child():
    """Job ids are guessable strings and the registry is process-wide.

    A parent steering someone else's child would be invisible to both: the other run's
    child would take a turn nobody in that run asked for.
    """
    child = _Child()
    sub = await _start(child, "first", continuable=True, parent_ctx=_Parent())
    assert await _until(lambda: sub.alive and not sub.busy)

    sent = await runtime_manager.send_to_child(sub.job_id, "more work", session_id="other-session")
    assert not sent.success and "another session" in sent.message


# --------------------------------------------------------------------------- #
# One transcript per child
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_report_and_a_result_land_in_one_transcript_in_order():
    """A child's findings and its answer are the same conversation.

    Two places to read would mean a parent that collects the result and never learns
    what the child said on the way — which is most of what a long-running child is for.
    """
    child = _Child(delay=0.1)
    sub = await _start(child, "investigate", parent_ctx=_Parent())

    await _report(sub.job_id, "the fixture is reversed, not the parser")
    assert await _until(lambda: job_manager.get(sub.job_id).status.is_final)

    transcript = job_manager.output(sub.job_id)
    assert transcript.index("fixture is reversed") < transcript.index("answer 1")


@pytest.mark.asyncio
async def test_a_report_from_a_foreground_child_still_reaches_its_parent():
    """A blocked parent cannot poll a job, so a foreground child's report would be
    written somewhere nobody ever opens.

    Folding it into the returned result is what keeps `report_tool` from meaning
    different things depending on how the parent happened to dispatch.
    """

    class _Reporting(_Child):
        async def handle(self, msg, ref):
            if isinstance(msg, TaskMessage):
                ctx = msg.kwargs.get("ctx")
                await _report(ctx.extra["report_job_id"], "found it in config.json")
            await super().handle(msg, ref)

    response = await runtime_manager.delegate(_Reporting(), "look for it", parent_ctx=_Parent())

    assert "answer 1" in response.message
    assert "found it in config.json" in response.message


@pytest.mark.asyncio
async def test_a_child_is_told_where_to_report_without_having_to_guess():
    """The id is on the child's context, not in its brief.

    A model asked to quote an id it was told in prose will eventually invent one, and an
    invented id writes a report into nothing.
    """
    child = _Child(delay=0.2)
    sub = await _start(child, "anything", parent_ctx=_Parent())

    assert await _until(lambda: child.turns)
    assert child.turns[0][1].extra["report_job_id"] == sub.job_id


# --------------------------------------------------------------------------- #
# Stopping and reaping
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_killing_the_job_actually_stops_the_child():
    """The registry must not report a child as dead while it goes on calling a model.

    That is the one failure the job registry exists to prevent, and it is the reason the
    child's driver — not its ref — is the handle the registry holds: a handle that dies
    without stopping the pump leaves exactly that lie behind.
    """
    child = _Child(delay=5.0)
    sub = await _start(child, "a long job", continuable=True, parent_ctx=_Parent())
    ref_name = sub.name

    assert job_manager.kill(sub.job_id) is True
    assert await _until(lambda: runtime_manager.get(ref_name) is None)
    assert not sub.alive
    assert job_manager.get(sub.job_id).status is JobStatus.KILLED


@pytest.mark.asyncio
async def test_what_the_child_said_before_a_kill_survives_it():
    """Stopping a child that was going nowhere must not destroy what it had already
    worked out — that is usually why the parent is stopping it."""
    child = _Child(delay=5.0)
    sub = await _start(child, "a long job", parent_ctx=_Parent())
    await _report(sub.job_id, "the build is broken upstream")

    job_manager.kill(sub.job_id)

    assert "build is broken upstream" in job_manager.output(sub.job_id)


@pytest.mark.asyncio
async def test_a_background_child_does_not_outlive_the_run_that_started_it():
    """Nothing else would ever stop it.

    The parent has concluded and its result is delivered; in a long-lived host the child
    would keep taking turns on a task whose answer nobody can collect any more, spending
    tokens against a run that is over.
    """
    child = _Child(delay=5.0)
    sub = await _start(child, "a long job", continuable=True, parent_ctx=_Parent())
    ref_name = sub.name

    runtime_manager.forget(SESSION)

    assert await _until(lambda: runtime_manager.get(ref_name) is None)
    assert runtime_manager.children(SESSION) == []


@pytest.mark.asyncio
async def test_reaping_one_session_leaves_another_session_s_children_alone():
    """The registry is process-wide and two runs can be live at once in a gateway.

    A reaper that walked every child would end a concurrent run's worker the moment any
    other run finished.
    """
    mine = await _start(_Child(delay=5.0), "mine", continuable=True, parent_ctx=_Parent())
    theirs = await _start(
        _Child(delay=5.0), "theirs", continuable=True, parent_ctx=_Parent(id="other-session")
    )

    runtime_manager.forget(SESSION)

    assert await _until(lambda: not mine.alive)
    assert theirs.alive
    assert [s.job_id for s in runtime_manager.children("other-session")] == [theirs.job_id]


# --------------------------------------------------------------------------- #
# What the plan-mode gate reads
# --------------------------------------------------------------------------- #
def test_handing_a_child_new_work_is_gated_like_the_dispatch_it_is():
    """`send_message_tool`'s effects are whatever the child then does.

    Declaring it read-only because "it only queues a string" would let a run held for
    plan approval do anything at all through a child it started earlier — through the
    one gate whose whole job is to stop that.
    """
    from agentevolver.tool.default.send_message import SendMessageTool

    tool = SendMessageTool()
    declaration = {"mutates": tool.mutates, "permission_mode": tool.permission_mode}
    assert action_is_allowed("tool", tool.name, declaration) is False


def test_publishing_subscriber_work_is_gated_like_dispatch():
    """Fan-out starts Agent turns, so plan mode must treat publish as an effect."""
    from agentevolver.tool.default.publish_event import PublishEventTool

    tool = PublishEventTool()
    declaration = {"mutates": tool.mutates, "permission_mode": tool.permission_mode}
    assert action_is_allowed("tool", tool.name, declaration) is False


def test_a_child_may_still_say_what_it_found_while_a_plan_is_being_approved():
    """A report changes nothing a person or a later run could observe, and findings are
    exactly what a planning run wants out of a child it already started."""
    from agentevolver.tool.default.report import ReportTool

    tool = ReportTool()
    declaration = {"mutates": tool.mutates, "permission_mode": tool.permission_mode}
    assert action_is_allowed("tool", tool.name, declaration) is True


def test_backgrounding_does_not_smuggle_a_dispatch_past_the_plan_gate():
    """An agent dispatch is refused in plan mode because its effects are whatever the
    child does, and that is no less true when the parent does not wait for it.

    The gate judges the *kind*, so this holds without anything being added — which is
    precisely the property worth pinning, because the tempting fix for "backgrounding is
    refused" is to make the kind judgeable.
    """
    assert action_is_allowed("agent", "general_agent", {"mutates": False}) is False
    assert action_is_allowed("agent", "general_agent", {"permission_mode": "read_only"}) is False


@pytest.mark.asyncio
async def test_the_dispatch_schema_offers_backgrounding_to_the_model():
    """A capability the schema does not mention cannot be reached.

    Backgrounding rides on the existing dispatch rather than a tool of its own, so the
    schema is the only place a model learns it exists — and the
    schema is `strict` with `additionalProperties: false`, which means an argument
    missing from it is not ignored but rejected.
    """
    from agentevolver.agent.server import AgentManagerServer

    properties = AgentManagerServer._dispatch_parameters()["properties"]
    assert "run_in_background" in properties
    assert "continuable" in properties
    assert "subscription_topics" in properties
    assert "continuable=true" in properties["subscription_topics"]["description"]


def test_dispatch_schema_bounds_prose_and_prefers_structured_artifacts():
    """One tool argument must not become an unbounded generated design document."""
    from agentevolver.agent.server import (
        AgentManagerServer,
        MAX_DELEGATED_TASK_CHARS,
        validate_dispatch_input,
    )

    properties = AgentManagerServer._dispatch_parameters()["properties"]
    assert properties["task"]["maxLength"] == MAX_DELEGATED_TASK_CHARS
    assert "specification file" in properties["task"]["description"]
    assert properties["files"]["maxItems"] > 0
    assert properties["acceptance"]["maxItems"] > 0

    with pytest.raises(ValueError, match="Write the detailed specification"):
        validate_dispatch_input({"task": "x" * (MAX_DELEGATED_TASK_CHARS + 1)})


def test_dispatch_contract_rejects_unbounded_structured_fields():
    from agentevolver.agent.server import (
        MAX_DELEGATION_CONTRACT_ITEM_CHARS,
        MAX_DELEGATION_CONTRACT_ITEMS,
        validate_dispatch_input,
    )

    with pytest.raises(ValueError, match="maximum"):
        validate_dispatch_input({
            "task": "implement the attached spec",
            "acceptance": ["ok"] * (MAX_DELEGATION_CONTRACT_ITEMS + 1),
        })
    with pytest.raises(ValueError, match="entry is"):
        validate_dispatch_input({
            "task": "implement the attached spec",
            "read_set": ["x" * (MAX_DELEGATION_CONTRACT_ITEM_CHARS + 1)],
        })


# --------------------------------------------------------------------------- #
# Outcome envelope returned to the parent
# --------------------------------------------------------------------------- #
def test_a_clean_finish_is_labelled_finished_with_its_cost():
    envelope = _delegation_summary(
        {"done": True, "stopped_by_constraint": False, "step": 40, "max_step": 50}
    )
    assert "finished" in envelope
    assert "40/50 steps" in envelope
    assert "PARTIAL" not in envelope


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (
            {"done": False, "stopped_by_constraint": False, "step": 50, "max_step": 50},
            "step ceiling",
        ),
        (
            {"done": False, "stopped_by_constraint": True, "step": 30, "max_step": 100},
            "resource limit",
        ),
        (
            {"done": False, "stopped_by_constraint": False, "step": 12, "max_step": 100},
            "12/100 steps",
        ),
    ],
    ids=("step-ceiling", "resource-limit", "unfinished"),
)
def test_an_unfinished_dispatch_is_labelled_partial(data, reason):
    """Every non-terminal outcome tells the parent why it must continue the work."""
    envelope = _delegation_summary(data)
    assert "PARTIAL" in envelope
    assert reason in envelope


def test_an_unbounded_child_does_not_show_a_sentinel_denominator():
    """The internal 1e8 sentinel is implementation noise, not a useful budget."""
    envelope = _delegation_summary({"done": True, "step": 5, "max_step": int(1e8)})
    assert "100000000" not in envelope
    assert "used 5 steps" in envelope


def test_missing_dispatch_data_yields_no_envelope():
    assert _delegation_summary(None) == ""
    assert _delegation_summary({}) == ""


def test_the_dispatch_envelope_is_one_trailing_tagged_line():
    """The status is append-only and cannot disturb the worker's own result text."""
    envelope = _delegation_summary({"done": True, "step": 1, "max_step": 5})
    assert envelope.startswith("\n\n[dispatch status:")
    assert envelope.rstrip().endswith("]")
    assert envelope.count("[dispatch status:") == 1


async def _invoke_agent_route(monkeypatch, delegated_response):
    """Invoke an agent route while replacing only the child lookup and delegation call."""
    import agentevolver.agent.server as server_module
    import agentevolver.runtime as runtime_module
    from agentevolver.agent.actor.general_agent import GeneralAgent

    with contextlib.redirect_stdout(io.StringIO()):
        agent = GeneralAgent(base_dir="/tmp/agentevolver-delegation-test")

    async def fake_get(name):
        return object()

    async def fake_delegate(child, task, **brief):
        return delegated_response

    # monkeypatch restores these process-global singletons after each case. The previous
    # standalone test assigned them directly and could leak its stubs into later files.
    monkeypatch.setattr(server_module.agent_manager, "get", fake_get)
    monkeypatch.setattr(runtime_module.runtime_manager, "delegate", fake_delegate)

    call = SimpleNamespace(
        input={"task": "implement the query command"},
        id="c1",
        name="code_agent",
    )
    result = await agent._invoke_capability(("agent", "code_agent"), call, ctx=None)
    return result[0], result[4]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            Response(
                type=ResponseType.AGENT,
                success=False,
                message="Implemented src/cmd/query.rs; the aging edge case is unfinished.",
                data={"done": False, "stopped_by_constraint": False, "step": 50, "max_step": 50},
            ),
            "step ceiling",
        ),
        (
            Response(
                type=ResponseType.AGENT,
                success=False,
                message="Wrote the parser; ran out of time before the tests.",
                data={"done": False, "stopped_by_constraint": True, "step": 20, "max_step": 50},
            ),
            "resource limit",
        ),
    ],
    ids=("step-ceiling", "resource-limit"),
)
async def test_partial_work_is_returned_as_an_observation(monkeypatch, response, expected):
    """Budget exhaustion preserves useful work instead of turning it into a hard error."""
    output, error = await _invoke_agent_route(monkeypatch, response)
    assert response.message in output
    assert "PARTIAL" in output and expected in output
    assert error is None


@pytest.mark.asyncio
async def test_a_clean_dispatch_still_carries_the_finished_envelope(monkeypatch):
    response = Response(
        type=ResponseType.AGENT,
        success=True,
        message="Done: query command implemented and verified.",
        data={"done": True, "stopped_by_constraint": False, "step": 30, "max_step": 50},
    )

    output, error = await _invoke_agent_route(monkeypatch, response)

    assert "query command implemented" in output
    assert "finished" in output and "30/50 steps" in output
    assert error is None


@pytest.mark.asyncio
async def test_a_genuine_early_dispatch_failure_keeps_its_error_flag(monkeypatch):
    """Only budget exhaustion is partial progress; provider failures remain failures."""
    response = Response(
        type=ResponseType.AGENT,
        success=False,
        message="The model could not be called: model not found.",
        data={"done": False, "stopped_by_constraint": False, "step": 1, "max_step": 50},
    )

    output, error = await _invoke_agent_route(monkeypatch, response)

    assert "model could not be called" in output
    assert error is not None and "model not found" in error
