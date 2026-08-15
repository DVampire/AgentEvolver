"""Promotion refuses anything it cannot prove came from the staged root it was handed.

This is the one place where a file an agent wrote inside a session becomes a file the
*next* session loads as code. Five registration hooks — memory, connector, skill,
environment, agent — call it, and none of them re-checks the paths, so whatever this
function accepts is what gets promoted.

Two claims carry that weight and neither is visible in the call signature. The component
must live under the staged root, checked after both paths are resolved, so a traversal
cannot be spelled with `..` or hidden behind a symlinked directory. And the staged root
must be a genuine sandbox extension root — the function reconstructs the sandbox from the
path's parent and refuses if the two disagree, which is what stops an arbitrary directory
being nominated as a staging area.

Until the coverage lane was introduced, no test executed a line of this file.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agentevolver.hook.promotion import promote_approved_component


@pytest.fixture
def staged(tmp_path: Path) -> Path:
    """A staged extension root holding one syntactically valid tool.

    The layout is not incidental: `ProjectSandbox` derives `extension_root` as
    `<project>/extension`, and promotion rebuilds the sandbox from the staged root's
    parent. A directory named anything else is rejected by design, which is what
    `test_a_directory_that_is_not_a_sandbox_extension_root_is_refused` covers.
    """
    root = tmp_path / "project" / "extension"
    (root / "tool").mkdir(parents=True)
    (root / "tool" / "promoted_tool.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root


@pytest.fixture
def shared(tmp_path: Path):
    """Point the durable extension tree at a throwaway directory for the whole call."""
    destination = tmp_path / "shared-extensions"
    destination.mkdir()
    with patch("agentevolver.hook.promotion.get_extension_root", return_value=str(destination)):
        yield destination


# --------------------------------------------------------------------------- #
# Containment
# --------------------------------------------------------------------------- #
def test_a_component_outside_the_staged_root_is_refused(staged: Path, tmp_path: Path):
    """The boundary the five calling hooks rely on and none of them repeats.

    A hook decides *whether* to promote; it passes on the paths it was given. If this
    check were missing, an accepted component could name any file on the host and have it
    copied into the tree that every later session imports.
    """
    outside = tmp_path / "elsewhere" / "not_staged.py"
    outside.parent.mkdir()
    outside.write_text("VALUE = 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside staged extension root"):
        promote_approved_component(str(staged), str(outside))


def test_a_traversal_spelled_with_dot_dot_is_refused(staged: Path, tmp_path: Path):
    """`resolve()` before comparing is what makes the containment check mean anything.

    This path is a string that starts with the staged root and still lands outside it.
    A prefix comparison on the raw text — the obvious way to write this — accepts it.
    """
    escaping = str(staged / ".." / ".." / "escaped.py")
    Path(tmp_path / "escaped.py").write_text("VALUE = 3\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside staged extension root"):
        promote_approved_component(str(staged), escaping)


def test_a_directory_that_is_not_a_sandbox_extension_root_is_refused(tmp_path: Path):
    """"The session staging root is never used as a durable extension directory."

    Promotion rebuilds the sandbox from the staged root's *parent* and requires the
    reconstruction to agree. Without it, any directory containing the component could be
    declared a staging area, and the containment check above would then be trivially
    satisfiable by choosing a root that contains whatever you wanted promoted.
    """
    fake_root = tmp_path / "project" / "not-called-extension"
    (fake_root / "tool").mkdir(parents=True)
    component = fake_root / "tool" / "promoted_tool.py"
    component.write_text("VALUE = 4\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid staged extension root"):
        promote_approved_component(str(fake_root), str(component))


# --------------------------------------------------------------------------- #
# The promotion itself
# --------------------------------------------------------------------------- #
def test_an_approved_component_lands_in_the_shared_tree_and_its_path_is_returned(
    staged: Path, shared: Path
):
    """The caller gets the destination back because the hook reports it to the user.

    Returning the *source* would look identical in every log line and be wrong in the one
    way that matters — it is the shared copy, not the staged one, that later sessions load.
    """
    component = staged / "tool" / "promoted_tool.py"

    destination = promote_approved_component(str(staged), str(component))

    assert Path(destination).is_file()
    assert Path(destination).read_text(encoding="utf-8") == "VALUE = 1\n"
    # Under the shared tree, not the session's staging area.
    assert shared in Path(destination).parents


def test_a_promotion_that_moved_more_than_one_thing_is_an_error_not_a_result(
    staged: Path, shared: Path
):
    """The last check, and the only one that fires after files have already moved.

    Everything above validates paths; this one validates the *outcome*, because the
    selection is expressed as a relative path and a path can match more than one thing.
    The function returns `promoted[0]` — so without this, a promotion that published two
    components would report the first and stay silent about the second, which is exactly
    the shape of an unnoticed publish.
    """
    from agentevolver.sandbox.project import ProjectSandbox

    two = {"promoted": [{"destination": "/a"}, {"destination": "/b"}]}
    with patch.object(ProjectSandbox, "promote", return_value=two):
        with pytest.raises(ValueError, match="exactly one promoted"):
            promote_approved_component(str(staged), str(staged / "tool" / "promoted_tool.py"))


def test_promoting_one_component_never_carries_its_neighbours_along(
    staged: Path, shared: Path
):
    """`relative_paths` selects exactly one; the count check is what proves it did.

    Staged roots hold everything an agent produced during a session. A promotion that
    quietly took the whole directory would publish components a human never approved —
    and the only signal would be this function returning a destination that looks fine.
    """
    (staged / "tool" / "never_approved.py").write_text("VALUE = 99\n", encoding="utf-8")

    destination = promote_approved_component(str(staged), str(staged / "tool" / "promoted_tool.py"))

    assert Path(destination).name == "promoted_tool.py"
    assert not (shared / "tool" / "never_approved.py").exists()
