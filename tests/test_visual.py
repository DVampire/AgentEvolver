"""A rendered task page is self-contained and opens from wherever it was written.

``render_task_page`` produces one complete HTML document that links ``task.css``
and ``task.js`` by a path computed relative to the page's own directory. That
computation is the load-bearing part: the page is written deep inside a session
tree and read back through whatever origin the browser reached, so an absolute
link resolves against the reader's machine and silently produces an unstyled
page — which looks like a CSS bug, not a path bug. The other half is escaping:
the title is a task's own text and lands inside an HTML attribute.
"""

import os
import re
from pathlib import Path

import pytest

from agentevolver.visual import asset_path, render_task_page


# --------------------------------------------------------------------------- #
# The assets the page expects to find
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "view",
    ["task", "prompt", "workflow", "memory", "plan", "request"],
)
def test_the_referenced_stylesheets_exist(view):
    """Every stylesheet a renderer names is a bare filename joined onto a directory.

    Nothing resolves it at import time, so a renamed or moved file produces a
    404 in the reader's browser and no error anywhere near the code that named
    it. This is the only place the two ends are checked against each other.
    """
    assert os.path.isfile(asset_path(view, "style.css"))


@pytest.mark.parametrize("view", ["task", "prompt", "workflow", "request"])
def test_the_referenced_scripts_exist(view):
    """``task.js`` renders the Markdown inside the section tags client-side, so a
    missing script is not a missing flourish — the page shows its raw source."""
    assert os.path.isfile(asset_path(view, "app.js"))


def test_asset_paths_are_absolute():
    """They are resolved against the package, not the caller's cwd.

    ``render_task_page`` takes the difference between an asset path and the
    output directory. If the asset path were relative to wherever the process
    happened to start, that difference would be meaningless and would still be a
    plausible-looking string.
    """
    assert os.path.isabs(asset_path("task", "style.css"))
    assert os.path.isabs(asset_path("task", "app.js"))


def test_shipped_html_assets_resolve_after_regrouping():
    root = Path(__file__).resolve().parents[1]
    folders = [root / "agentevolver/prompt", root / "agentevolver/workflow/default", root / "examples/tasks"]
    for folder in folders:
        for page in folder.rglob("*.html"):
            for reference in re.findall(r'(?:href|src)="([^"]+)"', page.read_text()):
                if "visual/" in reference and reference.endswith((".css", ".js")):
                    assert (page.parent / reference).resolve().is_file(), (page, reference)
    visual = root / "agentevolver/visual"
    assert not (visual / "css").exists()
    assert not (visual / "js").exists()


def test_workflow_iframe_embeds_regrouped_assets():
    from agentevolver.gateway.service import AgentGateway

    root = Path(__file__).resolve().parents[1]
    source = (root / "agentevolver/workflow/default/parallel_review.html").read_text()
    preview = AgentGateway._workflow_preview_document(source)
    assert len(re.findall(r"<style", preview)) == 2
    assert "<script src=" not in preview and '<script defer src=' not in preview
    assert "workflow-preview" in preview


# --------------------------------------------------------------------------- #
# What lands on disk
# --------------------------------------------------------------------------- #
def test_a_rendered_page_is_written_and_its_path_returned(tmp_path):
    """The return value is what callers hand on — to a log line, a link, a viewer —
    so returning ``None`` on success would be discovered by the reader, not here."""
    out = str(tmp_path / "task.html")
    assert render_task_page("<div class='task'>body</div>", out) == out
    assert os.path.isfile(out)


def test_the_body_is_inserted_verbatim(tmp_path):
    """The body already carries its own wrapper; re-wrapping would break the CSS."""
    out = str(tmp_path / "task.html")
    render_task_page('<div class="task"><h1>Title</h1></div>', out)
    page = open(out, encoding="utf-8").read()
    assert '<div class="task"><h1>Title</h1></div>' in page


def test_the_page_is_a_complete_document(tmp_path):
    """Nothing wraps this output later — it is opened directly, as a file or over a
    tunnel — so a fragment would be rendered in quirks mode with no charset."""
    out = str(tmp_path / "task.html")
    render_task_page("body", out)
    page = open(out, encoding="utf-8").read()
    assert page.startswith("<!DOCTYPE html>")
    assert "<head>" in page and "</head>" in page
    assert page.rstrip().endswith("</html>")


def test_a_missing_output_directory_is_created(tmp_path):
    """Callers name a path inside a session tree that may not exist yet, and a
    session's directories are made on first write rather than up front."""
    out = str(tmp_path / "deep" / "nested" / "task.html")
    render_task_page("body", out)
    assert os.path.isfile(out)


# --------------------------------------------------------------------------- #
# Links that survive the page being moved or served
# --------------------------------------------------------------------------- #
def test_assets_are_linked_relatively_so_the_page_travels(tmp_path):
    """An absolute path would break the moment the tree is moved or served."""
    out = str(tmp_path / "task.html")
    render_task_page("body", out)
    page = open(out, encoding="utf-8").read()
    href = re.search(r'<link rel="stylesheet" href="([^"]+)"', page).group(1)
    src = re.search(r'<script src="([^"]+)"', page).group(1)
    assert not os.path.isabs(href) and not os.path.isabs(src)


def test_the_linked_assets_resolve_from_the_page_s_own_directory(tmp_path):
    """Relative is necessary but not sufficient: a link can be relative and still
    point at nothing. This joins it back the way a browser would and opens it."""
    out = str(tmp_path / "sub" / "task.html")
    render_task_page("body", out)
    page = open(out, encoding="utf-8").read()
    out_dir = os.path.dirname(out)
    for pattern in (r'href="([^"]+)"', r'src="([^"]+)"'):
        target = re.search(pattern, page).group(1)
        assert os.path.isfile(os.path.join(out_dir, target))


def test_the_links_track_how_deep_the_page_is_written(tmp_path):
    """A hard-coded prefix passes both checks above and is still wrong.

    It would be relative, and it would resolve for pages written at one depth —
    the depth whoever wrote it happened to test with. Comparing two depths is
    what forces the path to actually be computed from the output directory.
    """
    shallow = str(tmp_path / "task.html")
    deep = str(tmp_path / "a" / "b" / "c" / "task.html")
    render_task_page("body", shallow)
    render_task_page("body", deep)
    shallow_href = re.search(r'href="([^"]+)"', open(shallow).read()).group(1)
    deep_href = re.search(r'href="([^"]+)"', open(deep).read()).group(1)
    assert deep_href.count("..") > shallow_href.count("..")


# --------------------------------------------------------------------------- #
# Text that came from a task
# --------------------------------------------------------------------------- #
def test_a_title_with_markup_cannot_break_out_of_its_attribute(tmp_path):
    """The title is a task's own text — it may contain anything."""
    out = str(tmp_path / "task.html")
    render_task_page("body", out, title='Fix "the" <script>alert(1)</script> bug')
    page = open(out, encoding="utf-8").read()
    meta = re.search(r'<meta name="description" content="([^"]*)"', page).group(1)
    assert "<script>" not in meta
    assert "&lt;script&gt;" in meta


def test_the_title_reaches_the_page(tmp_path):
    out = str(tmp_path / "task.html")
    render_task_page("body", out, title="Refactor the parser")
    assert 'content="Refactor the parser"' in open(out, encoding="utf-8").read()


def test_the_title_defaults_when_none_is_given(tmp_path):
    out = str(tmp_path / "task.html")
    render_task_page("body", out)
    assert 'content="Task"' in open(out, encoding="utf-8").read()


def test_unicode_in_the_body_survives(tmp_path):
    """Task text is routinely not ASCII, and both ends have to agree.

    The write must name UTF-8 rather than take the machine's default locale, and
    the document must declare the charset it was written in — get either wrong
    and the page is mojibake on someone else's machine while looking fine here.
    """
    out = str(tmp_path / "task.html")
    render_task_page("<div>修复这个缺陷</div>", out)
    assert "修复这个缺陷" in open(out, encoding="utf-8").read()
    assert 'charset="UTF-8"' in open(out, encoding="utf-8").read()
