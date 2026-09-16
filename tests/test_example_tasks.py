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
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import pytest

TASKS = Path(__file__).resolve().parents[1] / "examples" / "tasks"


class _TaskMarkup(HTMLParser):
    """Inspect the authored document before a browser can repair invalid nesting."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.elements = []
        self.sections = []
        self.markdown_elements = []
        self.wrappers = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent = self.stack[-1] if self.stack else None
        self.elements.append((tag, attrs))
        if parent and parent == ("div", {"class": "task"}):
            self.sections.append((tag, attrs))
        elif any(element in self.sections and element[1].get("data-format") != "html"
                 for element in self.stack):
            self.markdown_elements.append(tag)
        if attrs.get("class") == "task":
            assert tag == "div"
            self.wrappers += 1
        if tag not in {"meta", "link", "br", "hr", "img", "input"}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1][0] == tag, f"Unbalanced closing tag: {tag}"
        self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"meta", "link", "br", "hr", "img", "input"}:
            self.handle_endtag(tag)


@pytest.mark.parametrize("path", sorted(TASKS.rglob("*.html")),
                         ids=lambda p: str(p.relative_to(TASKS)))
def test_task_pages_share_rendering_assets_and_section_vocabulary(path):
    markup = _TaskMarkup()
    markup.feed(path.read_text(encoding="utf-8"))
    assert not markup.stack
    assert markup.wrappers == 1
    assert markup.sections
    assert {tag for tag, _ in markup.sections} <= {
        "objective", "requirements", "interface", "acceptance", "plan",
        "deliverables", "constraints", "notes",
    }
    assert not markup.markdown_elements, "Escape literal HTML in Markdown code examples"
    elements = markup.elements
    assert not any(tag == "style" or "style" in attrs for tag, attrs in elements)
    assert any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in elements)
    for tag, attribute, filename in [("link", "href", "style.css"), ("script", "src", "app.js")]:
        references = [attrs.get(attribute) for name, attrs in elements if name == tag]
        assert len(references) == 1 and references[0]
        assert (path.parent / references[0]).resolve() == (
            TASKS.parents[1] / "agentevolver/visual/task" / filename
        ).resolve()


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
    files = {p.stem[len("capability_") :] for p in TASKS.glob("capability_*.html")}
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
