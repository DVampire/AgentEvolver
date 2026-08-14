"""Conversation store: identity, transcript, and the resilience it promises.

The store is file-backed and stateless per call so a project directory stays readable
without the Gateway — you can open a conversation off disk and see what happened. Two
promises carry that. A conversation read back is the one that was written, including its
title and its transcript order, because this record is what the sidebar lists and what a
later run reloads as context. And recording must never break a run: `append` is called
from inside live agent work, so a project directory that has not materialized yet, a
half-written JSONL line from a killed process, or one unparseable meta file has to degrade
to less history rather than to an exception or an empty list.

The listing failures are the ones worth naming: a single corrupt file that empties the
sidebar looks exactly like "my conversations are gone", and a transcript returned newest
first would feed a reloaded agent its own history backwards.
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
    """Redirect the conversation paths into a throwaway tree.

    Only `path_manager.get` is replaced, so the store's own directory creation, atomic
    writes and read-back all run for real against real files.
    """

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


# --------------------------------------------------------------------------- #
# Identity: what a conversation is when read back off disk
# --------------------------------------------------------------------------- #
def test_a_created_conversation_reads_back_off_disk(store):
    """Nothing is held in memory between calls, so creation is only real if the file is.

    `view` in particular decides which sidebar a conversation appears in; a field lost in
    the round trip would default it back to `chat` and move the dialogue to another list.
    """
    made = store.create(OWNER, SESSION, view="canvas", title="Sketching")
    read = store.get(OWNER, SESSION, made.id)
    assert read.id == made.id
    assert read.view == "canvas"
    assert read.title == "Sketching"


def test_an_unknown_conversation_is_none_rather_than_an_error(store):
    """Callers ask for ids that came from a URL or a stale client, and must be able to."""
    assert store.get(OWNER, SESSION, "never-existed") is None


def test_listing_is_most_recently_used_first(store):
    """The sidebar's order is this sort, and `touch` is the only thing that changes it.

    Sorting on `created_at` would look correct on a fresh project and wrong forever after:
    the dialogue someone is working in right now would sink below one they opened once and
    abandoned.
    """
    first = store.create(OWNER, SESSION)
    second = store.create(OWNER, SESSION)
    first.touch()  # bump it past the newer one
    store.save(OWNER, first)
    assert [c.id for c in store.list(OWNER, SESSION)][0] == first.id
    assert {c.id for c in store.list(OWNER, SESSION)} == {first.id, second.id}


def test_listing_can_be_narrowed_to_one_view(store):
    """Chat, science and canvas share one directory and each shows only its own."""
    chat = store.create(OWNER, SESSION, view="chat")
    store.create(OWNER, SESSION, view="science")
    assert [c.id for c in store.list(OWNER, SESSION, view="chat")] == [chat.id]


def test_listing_an_untouched_project_is_empty_not_an_error(store):
    """A project only gets a conversations directory once something is written to it.

    Every project therefore starts in this state, so the first list of a new project is
    the common path, not an edge case.
    """
    assert store.list(OWNER, "no-such-project") == []


def test_one_corrupt_record_does_not_hide_the_others(store):
    """A single unreadable file must cost one entry, not the whole sidebar.

    `list` reads every `*.json` in the directory; if one bad parse propagated, a file
    truncated by a kill would present as "all my conversations disappeared", and the
    obvious next move — deleting the directory — would make it true.
    """
    good = store.create(OWNER, SESSION)
    broken = store._meta_path(OWNER, SESSION, "broken")
    broken.write_text("{not json", encoding="utf-8")
    assert [c.id for c in store.list(OWNER, SESSION)] == [good.id]


def test_renaming_an_absent_conversation_reports_none(store):
    """A rename that silently created the record would leave a titled shell with no history."""
    assert store.rename(OWNER, SESSION, "ghost", "title") is None


def test_delete_reports_whether_anything_was_there(store):
    """The return value distinguishes a deletion from a repeat, and the transcript goes too.

    Deletes arrive twice — a retried request, a double click. Reporting `True` both times
    would tell the client something was removed when nothing was. Leaving the JSONL behind
    would be worse: a later conversation reusing the id would inherit a stranger's
    transcript.
    """
    made = store.create(OWNER, SESSION)
    store.append(OWNER, SESSION, made.id, {"event": "one"})
    assert store.delete(OWNER, SESSION, made.id) is True
    assert store.delete(OWNER, SESSION, made.id) is False
    assert store.events(OWNER, SESSION, made.id) == []


# --------------------------------------------------------------------------- #
# Transcript: append-only, and never fatal
# --------------------------------------------------------------------------- #
def test_the_transcript_comes_back_oldest_first(store):
    """This is what a reloaded agent reads as its own history, so the order is the meaning."""
    made = store.create(OWNER, SESSION)
    for n in range(3):
        store.append(OWNER, SESSION, made.id, {"n": n})
    assert [e["n"] for e in store.events(OWNER, SESSION, made.id)] == [0, 1, 2]


def test_recording_against_an_unmaterialized_project_is_a_no_op(store):
    """`append` must never raise — the run outlives its bookkeeping.

    It is called from inside live agent work, where the project directory may not exist
    yet. An exception here would abort a task for the sake of a log line nobody had asked
    for.
    """
    store.append(OWNER, "not-created-yet", "c1", {"event": "x"})
    assert store.events(OWNER, "not-created-yet", "c1") == []


def test_one_unparseable_line_does_not_lose_the_rest(store):
    """A process killed mid-append leaves a partial line, and the file keeps growing after it.

    Reading the transcript by parsing until the first failure would truncate every
    conversation at the moment of an unrelated crash and silently discard everything
    written since.
    """
    made = store.create(OWNER, SESSION)
    store.append(OWNER, SESSION, made.id, {"n": 0})
    # A truncated record plus a blank line: what an interrupted write actually leaves.
    with store._events_path(OWNER, SESSION, made.id).open("a", encoding="utf-8") as fh:
        fh.write("{ broken\n\n")
    store.append(OWNER, SESSION, made.id, {"n": 1})
    assert [e["n"] for e in store.events(OWNER, SESSION, made.id)] == [0, 1]


def test_unicode_survives_the_round_trip(store):
    """Titles come from whatever the user typed, and the store writes JSON with escaping off.

    Both halves have to agree on UTF-8: a mismatch shows up as mojibake in the sidebar and
    as an unreadable transcript, and it is only ever noticed by users who do not write in
    ASCII.
    """
    made = store.create(OWNER, SESSION, title="中文标题")
    store.append(OWNER, SESSION, made.id, {"text": "你好"})
    assert store.get(OWNER, SESSION, made.id).title == "中文标题"
    assert store.events(OWNER, SESSION, made.id)[0]["text"] == "你好"


# --------------------------------------------------------------------------- #
# Attaching a submission to its conversation
# --------------------------------------------------------------------------- #
def test_a_submission_titles_an_unnamed_conversation(store):
    """The first message names the dialogue, because nobody names one before starting it."""
    made = store.create(OWNER, SESSION)
    noted = store.note_task(OWNER, SESSION, made.id, "task-1", "Explain the port registry")
    assert noted.title == "Explain the port registry"
    assert noted.task_ids == ["task-1"]


def test_an_existing_title_is_not_overwritten_by_a_later_submission(store):
    """Deriving the title on every submission would rename a conversation as it went on.

    A title someone chose, or one taken from the opening message, is the handle they use
    to find this dialogue again; the tenth message is the worst available description of
    it.
    """
    made = store.create(OWNER, SESSION, title="Chosen")
    noted = store.note_task(OWNER, SESSION, made.id, "task-1", "something else entirely")
    assert noted.title == "Chosen"


def test_the_same_task_is_not_recorded_twice(store):
    """Resubmits and retries reuse a task id, and `task_count` is shown to the user."""
    made = store.create(OWNER, SESSION)
    store.note_task(OWNER, SESSION, made.id, "task-1", "first")
    noted = store.note_task(OWNER, SESSION, made.id, "task-1", "again")
    assert noted.task_ids == ["task-1"]


# --------------------------------------------------------------------------- #
# Deriving a title from an opening message
# --------------------------------------------------------------------------- #
def test_a_title_collapses_whitespace():
    """A pasted message arrives with newlines and tabs; the sidebar is one line."""
    assert title_from("  hello \n\t world  ") == "hello world"


def test_a_long_opening_message_is_truncated_with_an_ellipsis():
    """The limit counts characters, and the marker is one character — not three dots.

    Getting that wrong pushes the result past `TITLE_LIMIT`, which is the number the
    sidebar layout is built around.
    """
    title = title_from("x" * (TITLE_LIMIT + 20))
    assert len(title) == TITLE_LIMIT + 1  # the ellipsis is one character
    assert title.endswith("…")


def test_an_empty_message_yields_an_empty_title():
    """`None` reaches this from submissions that carry files and no text."""
    assert title_from("") == ""
    assert title_from(None) == ""


# --------------------------------------------------------------------------- #
# What the sidebar is handed
# --------------------------------------------------------------------------- #
def test_an_unnamed_conversation_still_lists_with_a_label():
    """A conversation exists before its first message, and a blank row cannot be clicked."""
    assert Conversation(session_id=SESSION).summary()["title"] == "New conversation"


def test_the_summary_reports_how_many_submissions_it_holds():
    conversation = Conversation(session_id=SESSION, task_ids=["a", "b"])
    assert conversation.summary()["task_count"] == 2


def test_the_saved_record_is_valid_json_on_disk(store):
    """The point of the file layout is that it is readable without this code running.

    Asserting through `json.loads` rather than through `store.get` is deliberate: a store
    that could read back its own private encoding would pass every other test in this file
    while leaving a project directory nothing else can inspect.
    """
    made = store.create(OWNER, SESSION, title="t")
    on_disk = json.loads(store._meta_path(OWNER, SESSION, made.id).read_text())
    assert on_disk["session_id"] == SESSION
    assert on_disk["title"] == "t"
