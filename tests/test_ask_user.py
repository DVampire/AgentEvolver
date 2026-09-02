"""An agent can ask a person a question, and the answer gets back to the agent.

The pieces are all pre-existing — the runtime's suspend/resume rendezvous, the trace
stream the Gateway republishes — so the failures worth guarding are the joins between
them. Three would each strand a run in a way nothing reports: a question that is asked
but never announced leaves the agent suspended on an event no UI ever saw; a question
whose pending record is dropped on timeout is still offered to a UI, so a person
answers into a void and is told nothing; and an answer keyed differently from the wait
resumes nobody while both sides believe they did their part. The fourth is the review
contract — a `plan-review` whose approve label names none of its options, or which
carries no plan, asks a person to approve something they cannot see.
"""

import asyncio

import pytest

from agentevolver.conversation.question import (
    QuestionManagerServer,
    suspend_key,
    validate,
)
from agentevolver.conversation.types import (
    PendingQuestion,
    QuestionIntent,
    QuestionOption,
    UserAnswer,
    UserQuestion,
)
from agentevolver.gateway.service import AgentGateway
from agentevolver.runtime import runtime_manager
from agentevolver.tool.default.lifecycle.ask_user import AskUserTool


@pytest.fixture
def questions():
    """A manager built without ``__init__``.

    ``QuestionManagerServer`` is a singleton, so constructing it normally hands back
    the process-wide instance and leaks pending questions between tests.
    """
    manager = QuestionManagerServer.__new__(QuestionManagerServer)
    manager._pending = {}
    return manager


@pytest.fixture
def announced(monkeypatch):
    """Capture what the manager puts on the trace channel instead of emitting it.

    ``trace_manager.emit`` silently drops everything when the manager is not running,
    which is the state in a unit test — so a test that asserted against the real
    manager would pass whether or not anything was announced at all.
    """
    seen = []

    async def record(self, name, record_, payload):
        seen.append((name, record_, payload))

    monkeypatch.setattr(QuestionManagerServer, "_announce", record)
    return seen


async def wait_for_pending(manager, session_id, count=1):
    """Give ``ask`` time to register its pending record and suspend.

    ``ask`` awaits its announcement before it suspends, so a single ``sleep(0)``
    yields back before the record exists — and every test built on one would race
    the code it is testing rather than test it.
    """
    for _ in range(200):
        pending = manager.pending(session_id)
        if len(pending) >= count:
            return pending
        await asyncio.sleep(0.005)
    raise AssertionError(f"no question pending for {session_id!r}")


def one_question(**overrides):
    fields = {
        "id": "q1",
        "question": "Which store?",
        "options": [QuestionOption(label="SQLite"), QuestionOption(label="Postgres")],
    }
    fields.update(overrides)
    return UserQuestion(**fields)


class Ctx:
    """A stand-in for the AgentContext a tool is handed."""

    def __init__(self, id="run-1", name="code_agent", extra=None):
        self.id = id
        self.name = name
        self.extra = extra or {}


# --------------------------------------------------------------------------- #
# The rendezvous
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_answer_reaches_the_agent_that_is_waiting_for_it(questions, announced):
    """The whole point: ask blocks, answer unblocks it with what the person chose."""
    asking = asyncio.create_task(questions.ask([one_question()], session_id="run-1"))
    [pending] = await wait_for_pending(questions, "run-1")

    assert questions.answer(pending.id, [{"id": "q1", "selected": ["SQLite"]}]) is True
    assert await asking == [UserAnswer(id="q1", selected=["SQLite"])]


@pytest.mark.asyncio
async def test_a_question_waits_on_a_key_that_an_escalation_cannot_resume(questions):
    """Suspend keys are one flat namespace shared with escalation.

    Escalation keys on a bare ``task_id``. If a question keyed on a bare request id
    and the two ever coincided, a parent's ``reply_tool`` would silently answer a
    person's question — and the agent would act on guidance it never asked for.
    """
    assert suspend_key("abc") == "question:abc"
    assert suspend_key("abc") != "abc"


@pytest.mark.asyncio
async def test_answering_a_question_nobody_is_waiting_on_is_reported_not_raised(questions):
    """A second tab answering after the first is the person doing nothing wrong.

    Tempting to treat as an error, since the id resolves to nothing. But the UI has
    to tell "you were too late" from "the request was malformed", and only one of
    those is worth showing the person as a failure.
    """
    assert questions.answer("never-asked", [{"id": "q1", "selected": ["SQLite"]}]) is False


@pytest.mark.asyncio
async def test_a_question_that_times_out_is_no_longer_offered_to_a_ui(questions, announced):
    """The asker has given up, so the pending record must go with it.

    If it survived, `question.list` would keep showing a question whose waiter is
    gone: the person answers, is told nothing, and the run they were trying to
    unblock has already moved on.
    """
    # Far below any real budget: this test is about what is left behind, not waiting.
    with pytest.raises(TimeoutError):
        await questions.ask([one_question()], session_id="run-1", timeout=0.01)
    assert questions.pending("run-1") == []


@pytest.mark.asyncio
async def test_a_question_the_person_passed_over_comes_back_as_an_explicit_skip(questions):
    """A skipped question is answered with an empty selection, not omitted.

    The asker matches answers to questions by id. An omitted entry and an answer
    lost in transit look identical from there, so the skip is filled in here where
    the difference is still known.
    """
    asking = asyncio.create_task(
        questions.ask(
            [one_question(), one_question(id="q2", question="Anything else?")], session_id="run-1"
        )
    )
    [pending] = await wait_for_pending(questions, "run-1")
    questions.answer(pending.id, [{"id": "q1", "selected": ["Postgres"]}])
    answers = await asking

    assert {answer.id for answer in answers} == {"q1", "q2"}
    assert next(a for a in answers if a.id == "q2").selected == []


@pytest.mark.asyncio
async def test_an_answer_naming_a_question_that_was_not_asked_is_refused(questions):
    """A mismatched id means the UI answered the wrong request.

    Passing it through would resume the agent with an answer to something else,
    which reads as a legitimate reply and is impossible to trace back afterwards.
    """
    asking = asyncio.create_task(questions.ask([one_question()], session_id="run-1"))
    [pending] = await wait_for_pending(questions, "run-1")

    with pytest.raises(ValueError, match="not asked"):
        questions.answer(pending.id, [{"id": "some-other-question", "selected": ["x"]}])

    questions.answer(pending.id, [{"id": "q1", "selected": ["SQLite"]}])
    await asking


# --------------------------------------------------------------------------- #
# Reaching a person
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_asking_announces_the_question_before_it_starts_waiting(questions, announced):
    """Announce-then-suspend, in that order.

    Reversed, the agent is suspended on a rendezvous no UI has been told about; the
    ordering is the whole reason a person ever learns there is a question. The
    announcement carries the request id because that is what an answer is addressed
    to — an event describing the question but not naming it is unanswerable.
    """
    asking = asyncio.create_task(questions.ask([one_question()], session_id="run-1"))
    await wait_for_pending(questions, "run-1")

    [(name, record, _)] = announced
    assert name == "question.asked"
    assert record.public()["request_id"] == record.id
    assert record.public()["questions"][0]["question"] == "Which store?"

    questions.answer(record.id, [{"id": "q1", "selected": ["SQLite"]}])
    await asking


@pytest.mark.asyncio
async def test_a_question_asked_before_a_client_connected_is_still_findable(questions, announced):
    """`pending()` exists for the browser that reloaded mid-question.

    The live event is gone by then. Without a listable record the agent stays
    suspended and the UI has no way to discover that it owes an answer — a hang with
    no error anywhere.
    """
    asking = asyncio.create_task(questions.ask([one_question()], session_id="run-1"))
    await wait_for_pending(questions, "run-1")

    assert [record.session_id for record in questions.pending("run-1")] == ["run-1"]
    # Another project's tab must not be shown this project's question.
    assert questions.pending("run-2") == []

    questions.answer(questions.pending("run-1")[0].id, [{"id": "q1", "selected": ["SQLite"]}])
    await asking


def test_the_gateway_exposes_commands_to_list_and_answer_questions():
    """Gateway dispatch is by method name, so a missing handler fails only at runtime.

    ``handle`` looks up ``_command_<method>`` with ``getattr`` and returns
    "unknown_method" when it is absent. A renamed or dropped handler would therefore
    break the answer path with no import error and no failing unit test elsewhere.
    """
    for method in ("question.list", "question.answer"):
        assert getattr(AgentGateway, f"_command_{method.replace('.', '_')}", None) is not None


def test_a_pending_question_serializes_to_something_a_ui_can_answer():
    """`public()` must carry the request id, not only the question text."""
    record = PendingQuestion(session_id="run-1", questions=[one_question()])
    payload = record.public()
    assert payload["request_id"] == record.id
    assert payload["questions"][0]["options"][0]["label"] == "SQLite"


# --------------------------------------------------------------------------- #
# What may not be asked
# --------------------------------------------------------------------------- #
def test_a_request_with_no_questions_is_refused():
    """An empty batch would suspend the agent on a question nobody can answer."""
    with pytest.raises(ValueError, match="at least one question"):
        validate([])


def test_an_intent_whose_approve_label_names_no_option_is_refused():
    """The approve label is what a plan-review UI renders as the yes button.

    Nothing in the types can catch this: both fields are strings and both are
    present. If it slipped through, the UI would offer a choice the asker never
    made — and a generic UI, showing the real options, would let a person approve
    with a label the asker then reads as a decline.
    """
    with pytest.raises(ValueError, match="names none of its options"):
        validate(
            [
                one_question(
                    detail="the plan", intent=QuestionIntent(type="plan-review", approve="Yes")
                )
            ]
        )


def test_a_plan_review_with_nothing_to_review_is_refused():
    """`detail` is the plan; an intent without one asks for approval of the invisible.

    Caught at the asker rather than in each UI, because a UI that got this wrong has
    already shown the person the question.
    """
    with pytest.raises(ValueError, match="without the detail"):
        validate([one_question(intent=QuestionIntent(type="plan-review", approve="SQLite"))])


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_tool_returns_the_answer_as_json_the_model_can_read():
    """The result shape is the model's whole view of what the person said."""
    tool = AskUserTool()
    ctx = Ctx()

    async def answer_once():
        from agentevolver.conversation.question import question_manager

        [pending] = await wait_for_pending(question_manager, "run-1")
        question_manager.answer(
            pending.id, [{"id": "db", "selected": ["SQLite"], "custom": "and add an index"}]
        )

    asyncio.create_task(answer_once())
    response = await tool(questions=[{"id": "db", "question": "Which store?"}], ctx=ctx)

    assert response.success
    assert response.data == {
        "answers": [{"id": "db", "selected": ["SQLite"], "custom": "and add an index"}]
    }
    assert '"selected"' in response.message  # the model reads the message, not `data`


@pytest.mark.asyncio
async def test_the_tool_survives_nobody_answering_and_says_what_to_do_next(monkeypatch):
    """A timeout must come back as a readable failure, not an exception.

    A raised ``TimeoutError`` surfaces to the agent as "Action failed" with a bare
    class name, which tells the model nothing about whether to re-ask, wait, or go
    on. The one thing it must be told is not to re-ask.
    """
    monkeypatch.setattr("agentevolver.conversation.question.DEFAULT_QUESTION_TIMEOUT_S", 0.01)
    tool = AskUserTool()

    response = await tool(questions=[{"id": "db", "question": "Which store?"}], ctx=Ctx())

    assert response.success is False
    assert "Nobody answered" in response.message


@pytest.mark.asyncio
async def test_the_tool_rejects_an_empty_batch_without_suspending():
    """A malformed call is the model's to fix, so it comes back as a failed result."""
    response = await AskUserTool()(questions=[], ctx=Ctx())
    assert response.success is False
    assert "at least one question" in response.message


def test_the_tools_call_budget_outlasts_the_wait_it_performs():
    """The tool manager cancels a call that exceeds ``call_timeout_seconds``.

    If that budget were the shorter of the two, the wait would be cut off by the
    manager instead of by the question's own timeout — and the agent would get an
    opaque cancellation rather than the tool's "nobody answered, do not re-ask".
    """
    from agentevolver.conversation.question import DEFAULT_QUESTION_TIMEOUT_S

    assert AskUserTool().call_timeout_seconds > DEFAULT_QUESTION_TIMEOUT_S


def test_asking_a_person_is_declared_as_changing_nothing():
    """`mutates` and `permission_mode` are read by the plan-mode gate.

    A tool undeclared or declared as mutating would be refused in plan mode — which
    is exactly the situation where asking the person a question is most needed.
    """
    tool = AskUserTool()
    assert tool.mutates is False
    assert tool.permission_mode == "read_only"


@pytest.mark.asyncio
async def test_the_runtime_has_no_waiter_left_after_a_question_resolves():
    """A leaked future under the rendezvous key blocks the next question on that id.

    ``runtime_manager.suspend`` refuses a key that already has a live waiter, so an
    ask that failed to clean up turns the *following* ask into a hard error far from
    the cause.
    """
    from agentevolver.conversation.question import question_manager

    asking = asyncio.create_task(question_manager.ask([one_question()], session_id="run-9"))
    [pending] = await wait_for_pending(question_manager, "run-9")
    question_manager.answer(pending.id, [{"id": "q1", "selected": ["SQLite"]}])
    await asking

    assert suspend_key(pending.id) not in runtime_manager._pending
