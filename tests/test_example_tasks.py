"""The shipped example tasks, held to the conventions their filenames promise.

`examples/tasks/capability_*.html` is one smoke test per capability type — does the meta
agent actually reach a tool, a skill, a sub-agent, a connector, a plugin, an environment,
a workflow. The set is only meaningful if it is complete, and it was not: `workflow` had
no file, so the one capability whose registration rewrites its own artifact was the one
nothing exercised. Nothing said so, because a missing file looks exactly like a set that
was always six.

The other convention is that a task file's declared name matches the file holding it. A
copied-and-renamed task keeps the old `<meta name="name">`, and the run then reports
under a name that is not the one anybody asked for — which is how `capability_subagent`
survived a type called `agent`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pytest

TASKS = Path(__file__).resolve().parents[1] / "examples" / "tasks"


def _declared_name(path: Path) -> Optional[str]:
    match = re.search(r'<meta\s+name="name"\s+content="([^"]*)"', path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def test_every_capability_type_has_a_smoke_task():
    """One file per type the framework mounts, named for that type.

    Asserted in both directions: a type with no file is a capability nothing exercises,
    and a file with no type is a smoke test for something that no longer exists.
    """
    from agentevolver.capability.types import MOUNTED_TYPES

    # Everything an agent is handed, not only the six it *calls*: `environment` is mounted
    # the same way and reached through the same dispatch, so a smoke test that skipped it
    # would leave the one type whose actions have real side effects unexercised.
    types = {entry.type for entry in MOUNTED_TYPES}
    files = {p.stem[len("capability_"):] for p in TASKS.glob("capability_*.html")}
    assert files == types, (
        f"capability types with no smoke task: {sorted(types - files)}; "
        f"smoke tasks for no capability type: {sorted(files - types)}"
    )


@pytest.mark.parametrize("path", sorted(TASKS.glob("*.html")), ids=lambda p: p.stem)
def test_a_task_declares_the_name_of_the_file_it_lives_in(path):
    """A task that reports under someone else's name is worse than one with no name.

    Only files that declare one are checked. The long-form benchmark tasks carry a
    ``<title>`` and no ``<meta name="name">`` at all, and that is a different shape rather
    than a broken one — requiring the tag here would be inventing a convention to enforce
    it. What cannot stand is a declared name that disagrees with its file, which is
    exactly what a copied-and-renamed task leaves behind.
    """
    declared = _declared_name(path)
    if declared is None:
        pytest.skip("no declared name; this task is identified by its filename")
    assert declared == path.stem, f"{path.name} declares {declared!r}"
