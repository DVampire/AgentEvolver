"""Normalize authored documents and CLI input into Task submission context.

A task can be authored as a standalone document — an HTML file (rich layout,
the default) or a Markdown file — under ``examples/tasks/``. The document is the
detailed spec / plan / proposal for the run.

Two consumers, two forms:
  * the **agent** gets clean text (``content``) — never raw HTML markup, which
    would just waste tokens and confuse the model;
  * the **visual layer** gets renderable HTML (``html_body``) styled by
    ``agentevolver/visual/task/style.css``.

For ``.md`` the agent text is the raw markdown (already clean) and the view is
``markdown`` rendered to HTML. For ``.html`` the agent text is the tag-stripped
content and the view is the document body as-authored.
"""

from __future__ import annotations

import os
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from agentevolver.paths import P, path_manager
from agentevolver.task.types import TaskDocument
from agentevolver.visual import render_task_page


class _TextExtractor(HTMLParser):
    """Collect human-readable text, dropping <script>/<style> and tags.

    The task HTML is a *semantic tag-module*: a ``<div class="task">`` wrapper
    whose direct children are section blocks named by class
    (``<div class="objective">``, ``<div class="requirements">`` …). The visual
    label for each section lives in the class name + CSS, so a naive tag-strip
    would drop the section structure. We reconstruct it for the agent by
    emitting ``## <class>`` before each section block — same semantic source,
    rendered to text instead of pixels.
    """

    _SKIP = {"script", "style", "head", "meta", "link", "title"}
    _VOID = {"br", "img", "hr", "meta", "link", "input"}
    _BLOCK = {"p", "div", "br", "li", "tr", "section", "article",
              "ul", "ol", "table", "pre", "blockquote"}
    _WRAPPER_CLASS = "task"

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0
        self._stack: list[dict] = []

    @staticmethod
    def _classes(attrs):
        for k, v in attrs:
            if k == "class":
                return (v or "").split()
        return []

    def handle_starttag(self, tag, attrs):
        parent = self._stack[-1] if self._stack else None
        cls = self._classes(attrs)
        # A direct child of the .task wrapper is a section → emit its label.
        if parent and self._WRAPPER_CLASS in parent["cls"]:
            self._parts.append(f"\n## {cls[0] if cls else tag}\n")
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")
        if tag not in self._VOID:
            self._stack.append({"tag": tag, "cls": cls})

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in ("td", "th"):
            self._parts.append(" | ")   # keep table cells separated in text
        elif tag in self._BLOCK:
            self._parts.append("\n")
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i]["tag"] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = unescape("".join(self._parts))
        lines = [ln.strip() for ln in raw.splitlines()]
        out: list[str] = []
        for ln in lines:
            if ln or (out and out[-1]):
                out.append(ln)
        return "\n".join(out).strip()


def _strip_html(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return p.text()


def _extract_body(html: str) -> str:
    """Return the inner <body>…</body> if present, else the whole document."""
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else html.strip()


def _extract_title(html: str, fallback: str) -> str:
    # Mirror the prompt convention: metadata lives in <meta> tags in <head>.
    for meta_name in ("description", "name"):
        m = re.search(rf'<meta\s+name="{meta_name}"\s+content="([^"]*)"', html, re.IGNORECASE)
        if m and m.group(1).strip():
            return unescape(m.group(1).strip())
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m and m.group(1).strip():
        return unescape(m.group(1).strip())
    return fallback


def _md_to_html(md_text: str) -> str:
    try:
        import markdown  # 3.x
        return markdown.markdown(
            md_text, extensions=["fenced_code", "tables", "toc", "sane_lists"]
        )
    except Exception:
        # Graceful fallback: preformatted block (still styleable, just plain).
        from html import escape
        return f"<pre class='task-md-fallback'>{escape(md_text)}</pre>"


def _md_title(md_text: str, fallback: str) -> str:
    for line in md_text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return fallback


def load_task_document(path: str) -> TaskDocument:
    """Load a task document (.html or .md) into a :class:`TaskDocument`."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Task document not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    raw = open(path, "r", encoding="utf-8").read()
    fallback_title = os.path.splitext(os.path.basename(path))[0]

    if ext in (".html", ".htm"):
        body = _extract_body(raw)
        return TaskDocument(
            content=_strip_html(body),   # agent: clean markdown text (section labels reconstructed)
            html_body=body,              # view: markdown stays in the tags; task.js renders it client-side
            type="html",
            source_path=os.path.abspath(path),
            title=_extract_title(raw, fallback_title),
        )
    if ext in (".md", ".markdown"):
        return TaskDocument(
            content=raw.strip(),
            html_body=f'<div class="task-doc">{_md_to_html(raw)}</div>',
            type="md",
            source_path=os.path.abspath(path),
            title=_md_title(raw, fallback_title),
        )
    raise ValueError(f"Unsupported task document type {ext!r} (use .html or .md): {path}")


def add_task_args(parser: Any, default_task_file: Optional[str] = None) -> None:
    """Add the shared task-input arguments to an example launcher."""
    parser.add_argument(
        "--task", default=None,
        help="Inline task string (overrides --task-file).",
    )
    parser.add_argument(
        "--task-file", default=default_task_file,
        help="Path to a task document (.html or .md) under examples/tasks/.",
    )
    parser.add_argument(
        "--attach", nargs="*", default=None,
        help=(
            "Extra input files for the task. They are staged into the session "
            "workspace and handed to the agent with the task document."
        ),
    )


def resolve_task(
    args: Any,
    task_log_root: str,
    default_text: Optional[str] = None,
) -> Tuple[Optional[str], Optional[List[str]], Optional[Dict[str, Any]]]:
    """Resolve launcher input into ``content``, ``files``, and task metadata."""
    attachments = [str(path) for path in (getattr(args, "attach", None) or [])]
    if getattr(args, "task", None):
        return args.task, (attachments or None), None

    task_file = getattr(args, "task_file", None)
    if task_file:
        document = load_task_document(task_file)
        view_path = str(path_manager.under(
            task_log_root,
            P.LOG_TASK_VIEW,
            filename="task_view.html",
        ))
        render_task_page(document.html_body, view_path, title=document.title)
        metadata = {
            "task_doc": document.source_path,
            "task_view": view_path,
            "task_kind": document.type,
        }
        return document.content, [document.source_path, *attachments], metadata

    return default_text, (attachments or None), None
