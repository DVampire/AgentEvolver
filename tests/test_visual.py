"""Visual: rendering a task document into a standalone, styled page.

The page links its CSS/JS by *relative* path, so it opens from wherever it was
written — including a session tree the browser reaches over a tunnel. That makes
the relative-path computation the load-bearing part, along with escaping: the
title is a task's own text and lands inside an HTML attribute.
"""

import os
import re

import pytest

from agentevolver.visual import css_path, js_path, render_task_page


# -------------------------------------------------------------------- assets
@pytest.mark.parametrize("filename", ["task.css", "prompt.css", "workflow.css", "memory.css"])
def test_the_referenced_stylesheets_exist(filename):
    assert os.path.isfile(css_path(filename))


@pytest.mark.parametrize("filename", ["task.js", "prompt.js", "workflow.js"])
def test_the_referenced_scripts_exist(filename):
    assert os.path.isfile(js_path(filename))


def test_asset_paths_are_absolute():
    """They are resolved against the package, not the caller's cwd."""
    assert os.path.isabs(css_path("task.css"))
    assert os.path.isabs(js_path("task.js"))


# ------------------------------------------------------------------ rendering
def test_a_rendered_page_is_written_and_its_path_returned(tmp_path):
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
    out = str(tmp_path / "task.html")
    render_task_page("body", out)
    page = open(out, encoding="utf-8").read()
    assert page.startswith("<!DOCTYPE html>")
    assert "<head>" in page and "</head>" in page
    assert page.rstrip().endswith("</html>")


def test_a_missing_output_directory_is_created(tmp_path):
    out = str(tmp_path / "deep" / "nested" / "task.html")
    render_task_page("body", out)
    assert os.path.isfile(out)


# ------------------------------------------------------------- relative links
def test_assets_are_linked_relatively_so_the_page_travels(tmp_path):
    """An absolute path would break the moment the tree is moved or served."""
    out = str(tmp_path / "task.html")
    render_task_page("body", out)
    page = open(out, encoding="utf-8").read()
    href = re.search(r'<link rel="stylesheet" href="([^"]+)"', page).group(1)
    src = re.search(r'<script src="([^"]+)"', page).group(1)
    assert not os.path.isabs(href) and not os.path.isabs(src)


def test_the_linked_assets_resolve_from_the_page_s_own_directory(tmp_path):
    out = str(tmp_path / "sub" / "task.html")
    render_task_page("body", out)
    page = open(out, encoding="utf-8").read()
    out_dir = os.path.dirname(out)
    for pattern in (r'href="([^"]+)"', r'src="([^"]+)"'):
        target = re.search(pattern, page).group(1)
        assert os.path.isfile(os.path.join(out_dir, target))


def test_the_links_track_how_deep_the_page_is_written(tmp_path):
    shallow = str(tmp_path / "task.html")
    deep = str(tmp_path / "a" / "b" / "c" / "task.html")
    render_task_page("body", shallow)
    render_task_page("body", deep)
    shallow_href = re.search(r'href="([^"]+)"', open(shallow).read()).group(1)
    deep_href = re.search(r'href="([^"]+)"', open(deep).read()).group(1)
    assert deep_href.count("..") > shallow_href.count("..")


# ------------------------------------------------------------------ escaping
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
    out = str(tmp_path / "task.html")
    render_task_page("<div>修复这个缺陷</div>", out)
    assert "修复这个缺陷" in open(out, encoding="utf-8").read()
    assert 'charset="UTF-8"' in open(out, encoding="utf-8").read()
