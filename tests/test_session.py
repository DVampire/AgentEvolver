"""A context keeps its lineage across module boundaries, and an attachment stays out of the workspace.

Contexts are converted every time work crosses from one module type to another,
and each conversion is a chance to drop a field that only one subclass declares.
``parent_session_id`` is the one that matters: a tool that has lost it cannot
name who dispatched it, and the escalation channel has no other way to find the
parent. The second rule is about where a run's inputs live — an attachment from
outside the session is copied into ``log_root/inputs``, never the workspace,
because the workspace holds the agent's deliverable and anything left there
ships with the work when a run's workspace is packaged up.
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


# --------------------------------------------------------------------------- #
# Creating a context
# --------------------------------------------------------------------------- #
def test_a_created_context_gets_a_unique_id():
    """``ctx.id`` is the key memory, budgets and todos are filed under, so two
    contexts sharing one would silently merge two runs' state into one pile."""
    assert BaseContext.create().id != BaseContext.create().id


def test_a_created_context_starts_with_empty_payloads():
    """Empty *and* not shared. These are dict fields on a model that is created
    thousands of times; a single mutable default would make one run's ``extra``
    visible to every other, which reads as memory leaking between sessions."""
    ctx = BaseContext.create(name="thing")
    assert ctx.name == "thing"
    assert ctx.input == {} and ctx.extra == {}


def test_a_context_deliberately_has_no_workspace_root_field():
    """The working directory is owned by the global config, not carried per-context;
    a field here would let two sources of truth drift apart."""
    assert "workspace_root" not in BaseContext.model_fields


def test_a_session_context_accepts_the_extra_fields_managers_attach():
    """SessionContext is built by the gateway with fields the model never declared
    (``workspace_root`` among them). Under the default strict config that raises at
    session creation — the one moment where a failure looks like the gateway itself
    being broken."""
    ctx = SessionContext(id="s1", anything="allowed")
    assert ctx.anything == "allowed"


# --------------------------------------------------------------------------- #
# Converting one between module types
# --------------------------------------------------------------------------- #
def test_converting_from_nothing_produces_a_usable_context():
    """``from_context(None)`` is the entry point for a direct run with no caller.
    It has to mint a real context, not a half-built one whose ``extra`` is None."""
    ctx = BaseContext.from_context(None)
    assert ctx.id and ctx.input == {} and ctx.extra == {}


def test_conversion_carries_the_identity_and_payload_across():
    """The id survives, so the trace and memory attached to it stay attached.

    A conversion that minted a fresh id would look harmless — every field still
    present, nothing raised — and would detach the work from everything already
    recorded against it.
    """
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
    """``extra`` is the more specific answer: it was set deliberately by whoever
    built this context, while the field is whatever the source class happened to
    carry. Copying the field over it would rewrite a deliberate hand-off."""
    source = WithLineage(id="s1", parent_session_id="from-field",
                         extra={"parent_session_id": "already-set"})
    assert BaseContext.from_context(source).extra["parent_session_id"] == "already-set"


def test_absent_lineage_is_not_invented():
    """A top-level run has no parent, and ``extra`` carrying a None under that key
    is not the same as the key being absent — callers test for presence."""
    assert "parent_session_id" not in BaseContext.from_context(BaseContext(id="s1")).extra


# --------------------------------------------------------------------------- #
# The manifest that makes a session findable again
# --------------------------------------------------------------------------- #
class FakeSandbox:
    def __init__(self, root):
        self.project_root = str(root)


def test_the_manifest_records_a_session_s_identity(tmp_path):
    """This file is the whole reason a session survives a restart.

    The registry is in memory; the manifest on disk is what the gateway globs for
    when it comes back up. It is written by the shared session layer rather than
    by the gateway so a locally-started run is exactly as discoverable as one the
    browser created — when only the gateway wrote it, the same work was silently
    second-class depending on how it was launched.
    """
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


# --------------------------------------------------------------------------- #
# Staging a run's attachments
# --------------------------------------------------------------------------- #
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
    """A task document handed in from a checkout is not readable by the agent.

    ``check_session_path`` allows only the session's own roots, so passing the
    original path through would give the agent a file it is refused permission to
    open — reported as a missing file, from a path that plainly exists.
    """
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
    """Staging is not the place to adjudicate a typo: refusing here fails the whole
    submission, while passing it on lets the agent say which file it could not find."""
    missing = str(tmp_path / "gone.md")
    assert stage_input_files(None, {"files": [missing]})["files"] == [missing]


def test_several_attachments_keep_distinct_names_even_when_they_collide(roots, tmp_path):
    """Two files called ``task.md`` from different directories are ordinary.

    Names are taken from the source, so without the positional prefix the second
    copy silently overwrites the first and the agent reads the same document
    twice — with the right number of files and the wrong contents.
    """
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
    """Every input reaching a run passes through here, including ones where
    ``files`` means something else entirely to whoever wrote the caller."""
    assert stage_input_files(None, {"files": "not-a-list"})["files"] == "not-a-list"


def test_staging_does_not_mutate_the_caller_s_input(roots, tmp_path):
    """The caller keeps its dict — to retry with, to log, to submit again.

    Rewriting it in place would make a second submission stage the already-staged
    copies, and the first one to be retried after a session ended would point at
    a directory that had been cleaned up.
    """
    source = tmp_path / "task.md"
    source.write_text("x")
    original = {"files": [str(source)]}
    stage_input_files(None, original)
    assert original["files"] == [str(source)]


# --------------------------------------------------------------------------- #
# A manifest nothing read
# --------------------------------------------------------------------------- #
def test_a_written_manifest_can_be_read_back(tmp_path):
    """`write_session_manifest` had no reader anywhere in the tree.

    Its own docstring gives two reasons for existing: so a locally-started run is not
    second-class, and so a listing can lead with what was touched last. The gateway
    ordered its list from `self._sessions` — memory — so "what have I been working on"
    answered with whatever this process had seen, and a restart emptied it while the
    manifests sat on disk beside every project.
    """
    import json

    from agentevolver.session.project import SESSION_MANIFEST, read_session_manifest

    (tmp_path / SESSION_MANIFEST).write_text(json.dumps({
        "session_id": "s1", "name": "interactive", "owner": "local",
        "created_at": "2026-08-14T10:00:00+00:00",
        "updated_at": "2026-08-15T09:00:00+00:00",
    }), encoding="utf-8")

    manifest = read_session_manifest(tmp_path)

    assert manifest is not None
    assert manifest["session_id"] == "s1"


def test_a_directory_without_a_manifest_is_not_a_fault(tmp_path):
    """An older project simply predates the manifest; that is not an error to report."""
    from agentevolver.session.project import read_session_manifest

    assert read_session_manifest(tmp_path) is None


def test_a_manifest_that_will_not_parse_is_reported(monkeypatch, tmp_path):
    """Absent and corrupt both return None, so only the log separates them.

    A caller walking a tree cannot tell the two apart from the return value, and a
    corrupt manifest is a real fault where a missing one is not.
    """
    import agentevolver.logger as logger_module
    from agentevolver.session.project import SESSION_MANIFEST, read_session_manifest

    said = []
    monkeypatch.setattr(logger_module.logger, "warning", lambda message: said.append(message))
    (tmp_path / SESSION_MANIFEST).write_text("{ not json", encoding="utf-8")

    assert read_session_manifest(tmp_path) is None
    assert said, "a corrupt manifest was indistinguishable from an absent one"


def test_projects_are_listed_by_what_was_touched_last(tmp_path):
    """The order the manifest was written for.

    `created_at` would put a project someone has lived in all week below one opened once
    and abandoned.
    """
    import json

    from agentevolver.session.project import SESSION_MANIFEST, list_session_manifests

    for name, created, updated in (("old", "2026-01-01T00:00:00+00:00", "2026-08-15T09:00:00+00:00"),
                                   ("new", "2026-08-14T00:00:00+00:00", "2026-08-14T01:00:00+00:00")):
        directory = tmp_path / name
        directory.mkdir()
        (directory / SESSION_MANIFEST).write_text(json.dumps({
            "session_id": name, "created_at": created, "updated_at": updated,
        }), encoding="utf-8")

    assert [m["session_id"] for m in list_session_manifests(tmp_path)] == ["old", "new"]


def test_a_manifest_with_no_session_id_is_refused(tmp_path):
    """Identity is the one field the record exists to carry."""
    import json

    from agentevolver.session.project import SESSION_MANIFEST, read_session_manifest

    (tmp_path / SESSION_MANIFEST).write_text(json.dumps({"name": "nameless"}),
                                             encoding="utf-8")

    assert read_session_manifest(tmp_path) is None
