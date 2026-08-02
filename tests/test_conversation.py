"""Conversation store: identity, transcript, and the resilience it promises.

The store is file-backed and stateless per call so a project directory stays
readable without the Gateway. Two promises carry that: a conversation read back
off disk is the one that was written, and recording must never break a run —
a malformed record or a missing directory degrades instead of raising.
"""

import json

import pytest

from agentevolver.conversation.server import ConversationManagerServer
from agentevolver.conversation.types import TITLE_LIMIT, Conversation, title_from
from agentevolver.paths import P, path_manager

OWNER = "tester"
SESSION = "project-1"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Redirect the conversation paths into a throwaway tree."""

    def fake_get(key, **kw):
        base = tmp_path / kw.get("owner", "") / kw.get("session_id", "") / "conversations"
        if key == P.CONVERSATIONS:
            return base
        if key == P.CONVERSATION_META:
            return base / f"{kw['conversation_id']}.json"
        if key == P.CONVERSATION_EVENTS:
            return base / f"{kw['conversation_id']}.jsonl"
        raise AssertionError(f"unexpected path key {key}")

    monkeypatch.setattr("agentevolver.conversation.server.path_manager.get", fake_get)
    return ConversationManagerServer()


# ------------------------------------------------------------------- identity
def test_a_created_conversation_reads_back_off_disk(store):
    made = store.create(OWNER, SESSION, view="canvas", title="Sketching")
    read = store.get(OWNER, SESSION, made.id)
    assert read.id == made.id
    assert read.view == "canvas"
    assert read.title == "Sketching"


def test_an_unknown_conversation_is_none_rather_than_an_error(store):
    assert store.get(OWNER, SESSION, "never-existed") is None


def test_listing_is_most_recently_used_first(store):
    first = store.create(OWNER, SESSION)
    second = store.create(OWNER, SESSION)
    first.touch()  # bump it past the newer one
    store.save(OWNER, first)
    assert [c.id for c in store.list(OWNER, SESSION)][0] == first.id
    assert {c.id for c in store.list(OWNER, SESSION)} == {first.id, second.id}


def test_listing_can_be_narrowed_to_one_view(store):
    chat = store.create(OWNER, SESSION, view="chat")
    store.create(OWNER, SESSION, view="science")
    assert [c.id for c in store.list(OWNER, SESSION, view="chat")] == [chat.id]


def test_listing_an_untouched_project_is_empty_not_an_error(store):
    assert store.list(OWNER, "no-such-project") == []


def test_one_corrupt_record_does_not_hide_the_others(store):
    good = store.create(OWNER, SESSION)
    broken = store._meta_path(OWNER, SESSION, "broken")
    broken.write_text("{not json", encoding="utf-8")
    assert [c.id for c in store.list(OWNER, SESSION)] == [good.id]


def test_renaming_an_absent_conversation_reports_none(store):
    assert store.rename(OWNER, SESSION, "ghost", "title") is None


def test_delete_reports_whether_anything_was_there(store):
    made = store.create(OWNER, SESSION)
    store.append(OWNER, SESSION, made.id, {"event": "one"})
    assert store.delete(OWNER, SESSION, made.id) is True
    assert store.delete(OWNER, SESSION, made.id) is False
    assert store.events(OWNER, SESSION, made.id) == []


# ----------------------------------------------------------------- transcript
def test_the_transcript_comes_back_oldest_first(store):
    made = store.create(OWNER, SESSION)
    for n in range(3):
        store.append(OWNER, SESSION, made.id, {"n": n})
    assert [e["n"] for e in store.events(OWNER, SESSION, made.id)] == [0, 1, 2]


def test_recording_against_an_unmaterialized_project_is_a_no_op(store):
    """``append`` must never raise — the run outlives its bookkeeping."""
    store.append(OWNER, "not-created-yet", "c1", {"event": "x"})
    assert store.events(OWNER, "not-created-yet", "c1") == []


def test_one_unparseable_line_does_not_lose_the_rest(store):
    made = store.create(OWNER, SESSION)
    store.append(OWNER, SESSION, made.id, {"n": 0})
    with store._events_path(OWNER, SESSION, made.id).open("a", encoding="utf-8") as fh:
        fh.write("{ broken\n\n")
    store.append(OWNER, SESSION, made.id, {"n": 1})
    assert [e["n"] for e in store.events(OWNER, SESSION, made.id)] == [0, 1]


def test_unicode_survives_the_round_trip(store):
    made = store.create(OWNER, SESSION, title="中文标题")
    store.append(OWNER, SESSION, made.id, {"text": "你好"})
    assert store.get(OWNER, SESSION, made.id).title == "中文标题"
    assert store.events(OWNER, SESSION, made.id)[0]["text"] == "你好"


# ------------------------------------------------------------------ note_task
def test_a_submission_titles_an_unnamed_conversation(store):
    made = store.create(OWNER, SESSION)
    noted = store.note_task(OWNER, SESSION, made.id, "task-1", "Explain the port registry")
    assert noted.title == "Explain the port registry"
    assert noted.task_ids == ["task-1"]


def test_an_existing_title_is_not_overwritten_by_a_later_submission(store):
    made = store.create(OWNER, SESSION, title="Chosen")
    noted = store.note_task(OWNER, SESSION, made.id, "task-1", "something else entirely")
    assert noted.title == "Chosen"


def test_the_same_task_is_not_recorded_twice(store):
    made = store.create(OWNER, SESSION)
    store.note_task(OWNER, SESSION, made.id, "task-1", "first")
    noted = store.note_task(OWNER, SESSION, made.id, "task-1", "again")
    assert noted.task_ids == ["task-1"]


# ---------------------------------------------------------------- title_from
def test_a_title_collapses_whitespace():
    assert title_from("  hello \n\t world  ") == "hello world"


def test_a_long_opening_message_is_truncated_with_an_ellipsis():
    title = title_from("x" * (TITLE_LIMIT + 20))
    assert len(title) == TITLE_LIMIT + 1  # the ellipsis is one character
    assert title.endswith("…")


def test_an_empty_message_yields_an_empty_title():
    assert title_from("") == ""
    assert title_from(None) == ""


# ------------------------------------------------------------------- summary
def test_an_unnamed_conversation_still_lists_with_a_label():
    assert Conversation(session_id=SESSION).summary()["title"] == "New conversation"


def test_the_summary_reports_how_many_submissions_it_holds():
    conversation = Conversation(session_id=SESSION, task_ids=["a", "b"])
    assert conversation.summary()["task_count"] == 2


def test_the_saved_record_is_valid_json_on_disk(store):
    made = store.create(OWNER, SESSION, title="t")
    on_disk = json.loads(store._meta_path(OWNER, SESSION, made.id).read_text())
    assert on_disk["session_id"] == SESSION
    assert on_disk["title"] == "t"
