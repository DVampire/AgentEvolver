"""QuestionManagerServer — the seam where an agent stops and a person answers.

The mechanism is the runtime's suspend/resume rendezvous, the same one escalation
uses: the asker suspends on a key, and whoever answers resumes that key. The
difference is who the answerer is. Escalation's answerer is the parent MetaAgent,
which is another agent; this one's is a human, who is not in the process at all.

That changes two things and nothing else.

*Announcement.* A parent already has the question — it arrived in its inbox. A
person has to be *shown* it, so asking emits a trace event, which the Gateway
republishes to every connected client exactly as it republishes tool calls. No new
transport: the question rides the channel the UI is already reading.

*Reachability.* An answer arrives from outside the run, through a Gateway command,
possibly from a browser that connected after the question was asked. So the
pending question is held here and listable, not only broadcast. A UI that reloads
mid-question would otherwise leave the agent suspended on an answer nobody can
still see they owe.

Held in memory and session-local, like the job registry: a question outlives
neither the run that asked it nor the person who was there to answer.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Sequence

from agentevolver.conversation.types import PendingQuestion, UserAnswer, UserQuestion
from agentevolver.logger import logger
from agentevolver.utils import Singleton

#: How long an unanswered question is held before the asker is told nobody replied.
#: Generous, because the thing on the other end is a person who may be away from the
#: screen; bounded, because an agent suspended forever is a run that never ends and
#: never says why.
DEFAULT_QUESTION_TIMEOUT_S = 3600.0

#: Suspend keys are a single flat namespace shared with escalation, which keys on a
#: bare task_id. Prefixing keeps a question from resuming an escalation, or a reply
#: from answering a question, when the two ids happen to coincide.
_KEY_PREFIX = "question:"


def suspend_key(request_id: str) -> str:
    """The runtime rendezvous key a given question waits on."""
    return f"{_KEY_PREFIX}{request_id}"


def validate(questions: Sequence[UserQuestion]) -> None:
    """Reject the requests no type can reject, before anyone is shown them.

    Both checks are about an intent, and both are assertions the caller makes that
    only the caller's own question can confirm. A ``plan-review`` whose ``approve``
    label names none of its options puts a verdict in front of the person that the
    asker never offered; one with no ``detail`` asks them to approve something
    invisible. Caught here, at the asker, rather than in each UI — a UI that got it
    wrong would have already shown it.
    """
    if not questions:
        raise ValueError("ask_user_question requires at least one question")
    for question in questions:
        intent = question.intent
        if intent is None:
            continue
        labels = [option.label for option in question.options]
        if intent.approve not in labels:
            raise ValueError(
                f"question {question.id!r} declares intent {intent.kind!r} whose approve "
                f"label {intent.approve!r} names none of its options {labels}"
            )
        if not question.detail:
            raise ValueError(
                f"question {question.id!r} declares intent {intent.kind!r} without the "
                f"detail it is a review of"
            )


class QuestionManagerServer(metaclass=Singleton):
    """Ask a person something, and hold the question until they answer."""

    def __init__(self) -> None:
        self._pending: Dict[str, PendingQuestion] = {}

    # ------------------------------------------------------------------
    # Asking
    # ------------------------------------------------------------------

    async def ask(
        self,
        questions: Sequence[UserQuestion],
        *,
        session_id: str = "",
        task_id: str = "",
        agent_name: str = "",
        timeout: Optional[float] = None,
    ) -> List[UserAnswer]:
        """Put ``questions`` to a person and block until they answer.

        ``timeout`` defaults to :data:`DEFAULT_QUESTION_TIMEOUT_S`, resolved here
        rather than in the signature so the bound is one module-level fact a
        deployment can move. There is no "wait forever": an agent suspended on an
        answer that is never coming is a run that never ends and never says why.

        Raises ``ValueError`` for a request that cannot be shown honestly, and
        ``TimeoutError`` when nobody answers in time. Neither leaves a pending
        record behind: a question the asker has given up on must not still be
        offered to a UI, or someone answers into a void.
        """
        validate(questions)
        if timeout is None:
            timeout = DEFAULT_QUESTION_TIMEOUT_S
        record = PendingQuestion(
            session_id=session_id, task_id=task_id, agent_name=agent_name,
            questions=list(questions),
        )
        self._pending[record.id] = record

        from agentevolver.runtime import runtime_manager

        await self._announce("question.asked", record, {})
        logger.info(f"| 🙋 Question [{record.id}] from {agent_name or 'agent'}: "
                    f"{questions[0].question[:80]}")
        try:
            answers = await runtime_manager.suspend(suspend_key(record.id), timeout=timeout)
        except asyncio.TimeoutError as exc:
            # Re-raised as a plain TimeoutError carrying the request id. Only the
            # timeout is translated: a key collision or a cancelled run is a
            # different failure, and reporting either as "nobody answered" would
            # send the asker looking for a person who was never the problem.
            await self._announce("question.expired", record, {"error": str(exc)})
            raise TimeoutError(
                f"No answer to question [{record.id}] within {timeout}s"
            ) from exc
        finally:
            self._pending.pop(record.id, None)

        await self._announce("question.answered", record,
                             {"answers": [a.model_dump(mode="json") for a in answers]})
        logger.info(f"| 💬 Answer for [{record.id}]: "
                    f"{json.dumps([a.model_dump(mode='json') for a in answers], ensure_ascii=False)[:200]}")
        return answers

    # ------------------------------------------------------------------
    # Answering
    # ------------------------------------------------------------------

    def answer(self, request_id: str, answers: Sequence[Any]) -> bool:
        """Deliver a person's answer to whoever is waiting on ``request_id``.

        Returns whether anybody was still waiting. ``False`` is the ordinary
        outcome for a question that already timed out or was answered in another
        tab, and a UI needs to be able to tell that from an error — the person did
        nothing wrong, they were just second.
        """
        record = self._pending.get(request_id)
        if record is None:
            logger.info(f"| 💬 Answer for [{request_id}] arrived with nobody waiting")
            return False

        asked = {question.id for question in record.questions}
        parsed: List[UserAnswer] = []
        for item in answers:
            answer = item if isinstance(item, UserAnswer) else UserAnswer.model_validate(item)
            if answer.id not in asked:
                raise ValueError(
                    f"answer names question {answer.id!r}, which was not asked in "
                    f"request {request_id!r} (asked: {sorted(asked)})"
                )
            parsed.append(answer)

        # A question the person passed over is filled in as an explicit skip rather
        # than dropped. The asker matches answers to questions by id, and a missing
        # entry is indistinguishable from a lost one.
        answered = {answer.id for answer in parsed}
        parsed.extend(UserAnswer(id=question.id) for question in record.questions
                      if question.id not in answered)

        from agentevolver.runtime import runtime_manager

        return runtime_manager.resume(suspend_key(request_id), parsed)

    # ------------------------------------------------------------------
    # Looking
    # ------------------------------------------------------------------

    def pending(self, session_id: str = "") -> List[PendingQuestion]:
        """Questions still waiting on a person, oldest first.

        Scoped to one run when asked for, because two projects open in two tabs each
        have their own person in front of them.
        """
        records = [record for record in self._pending.values()
                   if not session_id or record.session_id == session_id]
        return sorted(records, key=lambda record: record.asked_at)

    def get(self, request_id: str) -> Optional[PendingQuestion]:
        return self._pending.get(request_id)

    def forget(self, session_id: str) -> None:
        """Drop a finished run's questions. Anyone still suspended times out."""
        for record in [r for r in self._pending.values() if r.session_id == session_id]:
            self._pending.pop(record.id, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _announce(self, name: str, record: PendingQuestion,
                        payload: Dict[str, Any]) -> None:
        """Put the question, or its resolution, on the channel the UI already reads.

        A trace event, not a new transport. The Gateway subscribes to the trace
        manager and republishes every event as ``trace.event`` tagged with the
        conversation that submitted the task — so a question reaches the right tab
        for the same reason a tool call does, and a client that already renders
        trace events needs no second subscription to see one.
        """
        from agentevolver.trace import trace_manager
        from agentevolver.trace.types import TraceEvent, TraceEventType

        try:
            await trace_manager.emit(TraceEvent(
                event_type=TraceEventType.CUSTOM,
                session_id=record.session_id or None,
                task_id=record.task_id or None,
                agent_name=record.agent_name or None,
                label=name,
                action_type="question",
                action_name=name,
                input=record.public(),
                output=payload or None,
            ))
        except Exception as exc:  # noqa: BLE001 — announcing must never break the ask
            logger.warning(f"| ⚠️ Could not announce {name} for [{record.id}]: {exc}")


question_manager = QuestionManagerServer()

__all__ = [
    "QuestionManagerServer",
    "question_manager",
    "validate",
    "suspend_key",
    "DEFAULT_QUESTION_TIMEOUT_S",
]
