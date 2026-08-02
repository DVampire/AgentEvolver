"""Session: context conversion, the manifest, and staging a run's attachments.

Two rules here are load-bearing and easy to break silently. Converting a context
between module types must preserve lineage — a tool that has lost
``parent_session_id`` cannot find the parent to escalate to. And an attachment
from outside the session is copied into ``log_root/inputs``, never the
workspace: the workspace holds the agent's deliverable, and where a run's
workspace is packaged up, an ``inputs/`` directory ships with the work.
"""

from pathlib import Path

import pytest

from agentevolver.session.project import SESSION_MANIFEST, stage_input_files, write_session_manifest
from agentevolver.session.types import BaseContext, SessionContext


class Narrowed(BaseContext):
    """A subclass that narrows ``name``, the way ToolContext does."""

    name: str = "unnamed"


class WithLineage(BaseContext):
    parent_session_id: str = None
    subtask_id: str = None


# ------------------------------------------------------------------ contexts
def test_a_created_context_gets_a_unique_id():
    assert BaseContext.create().id != BaseContext.create().id


def test_a_created_context_starts_with_empty_payloads():
    ctx = BaseContext.create(name="thing")
    assert ctx.name == "thing"
    assert ctx.input == {} and ctx.extra == {}


def test_converting_from_nothing_produces_a_usable_context():
    ctx = BaseContext.from_context(None)
    assert ctx.id and ctx.input == {} and ctx.extra == {}


def test_conversion_carries_the_identity_and_payload_across():
    source = BaseContext(id="s1", name="orig", input={"task": "x"}, extra={"k": "v"})
    converted = SessionContext.from_context(source)
    assert converted.id == "s1"
    assert converted.name == "orig"
    assert converted.input == {"task": "x"}
    assert converted.extra == {"k": "v"}


def test_a_subclass_that_narrows_name_falls_back_to_its_default():
    """``ToolContext.name`` is not Optional; a None must not fail validation."""
    assert Narrowed.from_context(BaseContext(id="s1", name=None)).name == "unnamed"


def test_lineage_survives_conversion_through_extra():
    """A converted context must still be able to name who dispatched it —
    the escalation channel has no other way to find the parent."""
    source = WithLineage(id="s1", parent_session_id="parent-1", subtask_id="sub-1")
    converted = BaseContext.from_context(source)
    assert converted.extra["parent_session_id"] == "parent-1"
    assert converted.extra["subtask_id"] == "sub-1"


def test_an_existing_extra_value_is_not_overwritten_by_lineage():
    source = WithLineage(id="s1", parent_session_id="from-field",
                         extra={"parent_session_id": "already-set"})
    assert BaseContext.from_context(source).extra["parent_session_id"] == "already-set"


def test_absent_lineage_is_not_invented():
    assert "parent_session_id" not in BaseContext.from_context(BaseContext(id="s1")).extra


def test_a_context_deliberately_has_no_workspace_root_field():
    """The working directory is owned by the global config, not carried per-context;
    a field here would let two sources of truth drift apart."""
    assert "workspace_root" not in BaseContext.model_fields


def test_a_session_context_accepts_the_extra_fields_managers_attach():
    ctx = SessionContext(id="s1", anything="allowed")
    assert ctx.anything == "allowed"


# ------------------------------------------------------------------ manifest
class FakeSandbox:
    def __init__(self, root):
        self.project_root = str(root)


def test_the_manifest_records_a_session_s_identity(tmp_path):
    import json

    write_session_manifest(FakeSandbox(tmp_path), session_id="s1", owner="alice", name="my project")
    payload = json.loads((tmp_path / SESSION_MANIFEST).read_text())
    assert payload["session_id"] == "s1"
    assert payload["owner"] == "alice"
    assert payload["name"] == "my project"
    assert payload["created_at"] and payload["updated_at"]


def test_an_explicit_creation_time_is_preserved_but_the_update_moves(tmp_path):
    """The project list leads with what was touched last, not what was born last."""
    import json

    write_session_manifest(FakeSandbox(tmp_path), session_id="s1", created_at="2020-01-01T00:00:00+00:00")
    payload = json.loads((tmp_path / SESSION_MANIFEST).read_text())
    assert payload["created_at"] == "2020-01-01T00:00:00+00:00"
    assert payload["updated_at"] != payload["created_at"]


def test_the_manifest_holds_identity_only_not_a_transcript(tmp_path):
    """The event log is a bounded ring; a restored transcript would be a partial
    one pretending to be complete."""
    import json

    write_session_manifest(FakeSandbox(tmp_path), session_id="s1")
    assert set(json.loads((tmp_path / SESSION_MANIFEST).read_text())) == {
        "session_id", "name", "owner", "created_at", "updated_at", "source_workspace",
    }


def test_an_unwritable_project_root_does_not_fail_the_run(tmp_path):
    """Bookkeeping must never be the thing that kills a session."""
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory")
    write_session_manifest(FakeSandbox(blocker / "root"), session_id="s1")  # must not raise


# ------------------------------------------------------------------- staging
@pytest.fixture
def roots(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    log = tmp_path / "log"
    workspace.mkdir()
    log.mkdir()
    monkeypatch.setattr("agentevolver.session.project.config.workspace_root", str(workspace), raising=False)
    monkeypatch.setattr("agentevolver.session.project.config.log_root", str(log), raising=False)
    return workspace, log


def test_an_outside_attachment_is_copied_into_the_inputs_directory(roots, tmp_path):
    workspace, log = roots
    source = tmp_path / "elsewhere" / "task.md"
    source.parent.mkdir()
    source.write_text("the task")

    staged = stage_input_files(None, {"files": [str(source)]})["files"]
    assert staged == [str(log / "inputs" / "000_task.md")]
    assert Path(staged[0]).read_text() == "the task"


def test_attachments_never_land_in_the_workspace(roots, tmp_path):
    """The workspace holds the deliverable; an attachment shipped with it would
    end up in the packaged output."""
    workspace, _ = roots
    source = tmp_path / "task.md"
    source.write_text("x")
    stage_input_files(None, {"files": [str(source)]})
    assert list(workspace.iterdir()) == []


def test_a_file_already_inside_the_workspace_is_left_where_it_is(roots):
    """Gateway uploads live in the workspace already; copying them would duplicate."""
    workspace, log = roots
    existing = workspace / "uploaded.txt"
    existing.write_text("x")
    staged = stage_input_files(None, {"files": [str(existing)]})["files"]
    assert staged == [str(existing.resolve())]
    assert not (log / "inputs").exists()


def test_a_missing_path_is_passed_through_so_the_agent_can_report_it(roots, tmp_path):
    missing = str(tmp_path / "gone.md")
    assert stage_input_files(None, {"files": [missing]})["files"] == [missing]


def test_several_attachments_keep_distinct_names_even_when_they_collide(roots, tmp_path):
    first = tmp_path / "a" / "task.md"
    second = tmp_path / "b" / "task.md"
    for path in (first, second):
        path.parent.mkdir()
        path.write_text(path.parent.name)

    staged = stage_input_files(None, {"files": [str(first), str(second)]})["files"]
    assert len(set(staged)) == 2
    assert Path(staged[0]).read_text() == "a"
    assert Path(staged[1]).read_text() == "b"


def test_input_without_files_is_returned_unchanged(roots):
    assert stage_input_files(None, {"task": "x"}) == {"task": "x"}


def test_a_non_list_files_value_is_left_alone(roots):
    assert stage_input_files(None, {"files": "not-a-list"})["files"] == "not-a-list"


def test_staging_does_not_mutate_the_caller_s_input(roots, tmp_path):
    source = tmp_path / "task.md"
    source.write_text("x")
    original = {"files": [str(source)]}
    stage_input_files(None, original)
    assert original["files"] == [str(source)]
