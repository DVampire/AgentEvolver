"""Type definitions for the conversation module.

A **conversation** is one line of dialogue inside a project. It is the middle
level of three:

===============  =========================================  ==================
Level            Owns                                       Identified by
===============  =========================================  ==================
project          workspace files, kernels, containers        ``session_id``
**conversation** transcript, agent memory, budgets, todos    ``conversation_id``
task             one submission's trajectory and traces      ``task_id``
===============  =========================================  ==================

The split matters because the two halves scale differently. A project's files
and kernels are *resources*: one set, shared by every view and every dialogue
in it. Memory and budgets are *state*: they must not leak between lines of
work, or a fresh question inherits the last one's context and spends its
tokens.

That is why ``ctx.id`` — the scope of everything an agent accumulates — is a
conversation id, while anything that costs a container stays keyed by project.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.utils import make_id

#: Which view a conversation belongs to. The transcript is the same shape in
#: each; the view decides what is rendered beside it (files, a canvas, a
#: notebook) and lets the sidebar list one view's dialogues on their own.
ConversationView = Literal["chat", "science", "canvas"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Conversation(BaseModel):
    """One line of dialogue in a project."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=make_id, description="Unique conversation id.")
    session_id: str = Field(description="The project this conversation belongs to.")
    view: ConversationView = Field(default="chat", description="Which view opened it.")
    #: Taken from the first message rather than asked for. A dialogue that has
    #: to be named before it starts is a dialogue nobody names.
    title: str = Field(default="", description="Human label, derived from the opening message.")
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    task_ids: List[str] = Field(default_factory=list, description="Submissions made in this conversation.")

    #: Set for the record that adopted a pre-conversation transcript, so the
    #: migration is visible rather than silently rewriting history.
    migrated: bool = Field(default=False)

    def summary(self) -> Dict[str, Any]:
        """What the sidebar needs to list this conversation."""
        return {
            "conversation_id": self.id,
            "session_id": self.session_id,
            "view": self.view,
            "title": self.title or "New conversation",
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "task_count": len(self.task_ids),
        }

    def touch(self) -> None:
        self.updated_at = _now()


#: How much of the opening message becomes the title.
TITLE_LIMIT = 60


def title_from(text: str) -> str:
    """Derive a conversation title from its opening message.

    Sessions used to be named ``web`` or ``interactive`` by whoever created
    them, so a sidebar of ten of them said nothing about any of them.
    """
    line = " ".join((text or "").split())
    if len(line) <= TITLE_LIMIT:
        return line
    return line[:TITLE_LIMIT].rstrip() + "…"


# ---------------------------------------------------------------------------
# Asking the human
# ---------------------------------------------------------------------------
#
# A conversation is the only place in the system where a person is present, so a
# question addressed to one belongs here rather than in the tool that asks it.
# ``escalate_tool`` already had this shape — a caller blocks, someone else answers
# — but the answerer there is the parent MetaAgent. Nothing could reach a human.


class QuestionOption(BaseModel):
    """One selectable answer offered to the person."""

    label: str = Field(description="Short label the UI shows and the answer echoes back.")
    description: str = Field(default="", description="One sentence on the tradeoff, for a UI that has room for it.")


class QuestionIntent(BaseModel):
    """A caller's claim that this question *is* a known kind of decision.

    A UI that recognises ``kind`` may render the question as that decision instead
    of as a generic menu. It changes presentation only: the answer that comes back
    names the same option labels either way, so the asker reads one shape of reply
    whether or not the UI understood the tag.
    """

    type: Literal["plan-review"] = Field(description="Which known decision this is.")
    #: Which label approves, named rather than inferred from option order — no UI
    #: should have to guess the verdict from a position in a list.
    approve: str = Field(description="The option label that approves; every other option declines.")


class UserQuestion(BaseModel):
    """One question put to a person."""

    id: str = Field(default_factory=make_id, description="Caller-chosen id, echoed in the answer so a batch can be matched up.")
    question: str = Field(description="The question itself.")
    #: Supporting text shown beside the question. Separate from the options because a
    #: plan under review is not a thing you can pick — it is what you are picking about.
    detail: str = Field(default="", description="Supporting text rendered with the question, kept out of the option labels.")
    header: str = Field(default="", description="Short heading, e.g. 'Confirm' or 'Choose mode'.")
    options: List[QuestionOption] = Field(default_factory=list, description="Choices a UI can render as a menu.")
    multi_select: bool = Field(default=False, description="Whether more than one option may be chosen.")
    intent: Optional[QuestionIntent] = Field(default=None, description="Presentation intent for a UI that knows the tag.")


class UserAnswer(BaseModel):
    """What the person answered for one question.

    ``selected`` and ``custom`` are both present because free text is not always a
    replacement for the menu: on a multi-select it supplements the chosen labels,
    and on a single-select it overrides them. An empty ``selected`` with no
    ``custom`` is a skip, which is a real answer — the person saw it and moved on.
    """

    id: str = Field(description="The question this answers.")
    selected: List[str] = Field(default_factory=list, description="Chosen option labels.")
    custom: str = Field(default="", description="Free-text answer, if the person typed one.")


class PendingQuestion(BaseModel):
    """A question that has been asked and not yet answered.

    Held so a UI that connects *after* the question was asked can still find it. A
    live event stream alone would strand the agent whenever the browser reloaded
    between the question and the answer.
    """

    id: str = Field(default_factory=make_id, description="Request id; what an answer is addressed to.")
    session_id: str = Field(default="", description="Which run is waiting.")
    task_id: str = Field(default="", description="Which task is waiting. Routes the event to a conversation.")
    agent_name: str = Field(default="", description="Who is asking.")
    asked_at: str = Field(default_factory=_now)
    questions: List[UserQuestion] = Field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        """The record as a UI receives it."""
        return {
            "request_id": self.id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "asked_at": self.asked_at,
            "questions": [q.model_dump(mode="json") for q in self.questions],
        }


__all__ = [
    "Conversation",
    "ConversationView",
    "title_from",
    "TITLE_LIMIT",
    "QuestionOption",
    "QuestionIntent",
    "UserQuestion",
    "UserAnswer",
    "PendingQuestion",
]
