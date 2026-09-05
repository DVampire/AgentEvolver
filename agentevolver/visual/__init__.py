"""Visual assets for AgentEvolver — CSS, JS, templates, and rendering helpers."""

import os
from html import escape

from agentevolver.paths import path_manager

from .benchmark import BenchmarkMonitor, build_snapshot


def asset_path(view: str, filename: str) -> str:
    """Resolve a view's resource through PathManager, independent of the cwd."""
    return str(path_manager.package_resource("visual", view, filename))


def render_task_page(html_body: str, out_path: str, title: str = "Task") -> str:
    """Render a task document body into a standalone, styled HTML page.

    Mirrors the prompt's page shell (meta_agent.html): metadata as ``<meta>``
    tags in ``<head>``, ``task/style.css`` + ``task/app.js`` linked there, and the body
    inserted directly (the body already carries its ``<div class="task">``
    wrapper, or a ``<div class="task-doc">`` for Markdown tasks). ``task/app.js``
    renders the Markdown inside the section tags client-side. Returns ``out_path``.
    """
    out_dir = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(out_dir, exist_ok=True)
    css_rel = os.path.relpath(asset_path("task", "style.css"), start=out_dir)
    js_rel = os.path.relpath(asset_path("task", "app.js"), start=out_dir)
    page = "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="UTF-8">',
        f'  <meta name="description" content="{escape(title)}">',
        f'  <link rel="stylesheet" href="{escape(css_rel)}">',
        f'  <script src="{escape(js_rel)}"></script>',
        "</head>",
        "<body>",
        html_body,
        "</body>",
        "</html>",
    ])
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return out_path


__all__ = [
    "BenchmarkMonitor",
    "build_snapshot",
    "asset_path",
    "render_task_page",
]
