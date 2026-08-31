"""A replacement that matches more than once is refused, not applied to the first hit.

`edit_file_tool` already enforced this, and nothing pinned it. That is the dangerous shape:
the rule is four lines in the middle of a method, it has no test, and every plausible
"simplification" of that method deletes it. Replacing only the first match is what
`str.replace(old, new, 1)` does by default and what a reader would assume the code means —
so the failure mode is a refactor that keeps the tool working on every example anyone tries
by hand, while silently editing the wrong site in any file where the snippet repeats.

The cost is asymmetric and invisible. A model that meant one call site and matched three
gets a success message and a file with two corruptions it will not look at again; a model
that is refused gets told to add context and retries. So the refusal must survive, and so
must its message — "add more context" is what makes the retry work instead of a loop.
"""

import asyncio

from agentevolver.tool.default.edit_file import EditFileTool


def _edit(path, old, new):
    # These tests isolate replacement semantics on pytest's temporary directory, which
    # deliberately sits outside the configured agent workspace. Full access is explicit
    # here; production calls use the registered tool's workspace fence.
    tool = EditFileTool(permission_mode="danger_full_access")
    return asyncio.run(tool(path=str(path), old_string=old, new_string=new))


def test_a_string_that_appears_twice_is_refused(tmp_path):
    """Two matches means the model's intent is ambiguous, and guessing is unrecoverable.

    Applying it to the first hit would return success, so nothing downstream — not the
    agent, not a later test run — has any signal that the wrong line changed.
    """
    target = tmp_path / "conf.py"
    target.write_text("timeout = 30\nretries = 30\n", encoding="utf-8")

    result = _edit(target, "= 30", "= 60")

    assert result.success is False
    assert "appears 2 times" in result.message
    assert target.read_text(encoding="utf-8") == "timeout = 30\nretries = 30\n"


def test_the_refusal_tells_the_model_how_to_retry(tmp_path):
    """A refusal the model cannot act on becomes a retry loop on the identical call.

    The recovery is specific — widen `old_string` until it is unique — and the model only
    knows that because the message says so. A bare "edit rejected" costs the same call and
    buys nothing.
    """
    target = tmp_path / "conf.py"
    target.write_text("a = 1\nb = 1\n", encoding="utf-8")

    result = _edit(target, "= 1", "= 2")

    assert "unique" in result.message


def test_a_string_that_appears_once_is_applied(tmp_path):
    """The rule has to be a uniqueness check, not a blanket refusal.

    A check written against the wrong count — `>= 1` instead of `> 1` — would pass the test
    above while making the tool useless, and the tool would still report failures honestly,
    so nothing else would notice.
    """
    target = tmp_path / "conf.py"
    target.write_text("timeout = 30\nretries = 3\n", encoding="utf-8")

    result = _edit(target, "timeout = 30", "timeout = 60")

    assert result.success is True
    assert target.read_text(encoding="utf-8") == "timeout = 60\nretries = 3\n"


def test_a_string_that_appears_nowhere_is_refused_as_a_stale_read(tmp_path):
    """Zero matches is a different diagnosis from two, and the message has to say which.

    It nearly always means the file moved on since the model read it — so the fix is to
    re-read, not to add context. Collapsing both counts into one "edit failed" sends the
    model to the wrong recovery.
    """
    target = tmp_path / "conf.py"
    target.write_text("timeout = 30\n", encoding="utf-8")

    result = _edit(target, "timeout = 45", "timeout = 60")

    assert result.success is False
    assert "not found" in result.message
    assert "read_file_tool" in result.message


def test_an_empty_new_string_deletes_the_unique_match(tmp_path):
    """Deletion is expressed as a replacement with nothing, so it runs the same check.

    Worth its own case because an implementation that guards on `if new_string:` before
    writing would turn a delete into a silent no-op that still reports success.
    """
    target = tmp_path / "conf.py"
    target.write_text("keep = 1\ndrop = 2\n", encoding="utf-8")

    result = _edit(target, "drop = 2\n", "")

    assert result.success is True
    assert target.read_text(encoding="utf-8") == "keep = 1\n"
