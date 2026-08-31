"""Every bash command's full output is archived to the session's bash log.

The tool result the model reads is still bounded by the universal output policy, but a
durable, complete copy is written beside it under ``<session>/log/bash`` — named by the
job id for a background run (so the file and ``job__output(job_id=...)`` are visibly the
same handle, the way Claude Code names a task's output file by the task id) and by a
timestamp for a one-shot foreground call that has no such handle. Archiving never fails
the command: a command that ran is a command that ran.
"""

import os

import pytest

import agentevolver.tool.default.bash as bash
from agentevolver.paths import P, path_manager


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Point the whole layout at a temp dir and bind a session, so archives land there."""
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    path_manager.bind_session(owner="o", session_id="s")
    return tmp_path


def test_foreground_output_is_archived_verbatim(session):
    text = "STDOUT:\n" + ("line\n" * 5000)  # larger than any inline excerpt cap
    path = bash._write_bash_archive("echo hi", text)
    assert path is not None
    body = open(path, encoding="utf-8").read()
    assert body.startswith("$ echo hi\n")  # header names the command
    assert text in body  # complete — nothing dropped
    # under the session's bash log, and a timestamp name (no handle to refer to it by)
    assert os.path.dirname(path) == str(path_manager.get(P.SESSION_BASH))
    assert path.endswith(".txt") and os.path.basename(path)[0].isdigit()


def test_background_file_is_named_by_job_id(session):
    path = bash._bash_archive_path(stem="job_ab12cd34")
    assert path is not None and os.path.basename(path) == "job_ab12cd34.txt"
    # and it can be created empty (header only), the way a background job starts it
    written = bash._write_bash_archive("python train.py", "", path=path)
    assert written == path
    assert open(path, encoding="utf-8").read().startswith("$ python train.py\n")


def test_archive_note_is_appended_only_when_archived():
    assert bash._with_archive_note("done", None) == "done"
    noted = bash._with_archive_note("done", "/x/y.txt")
    assert noted.startswith("done") and "/x/y.txt" in noted


def test_archiving_never_raises_when_path_unresolvable(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no bound session")

    monkeypatch.setattr(bash.path_manager, "get", _boom)
    assert bash._bash_archive_path() is None
    assert bash._write_bash_archive("cmd", "some output") is None
