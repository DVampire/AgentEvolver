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

import json
import os
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Container, Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------------
# Input manifests — attachments a task declares, and the half an agent may read
# ---------------------------------------------------------------------------

#: Where a task document declares what was attached to it and who each part is for.
MANIFEST_MARKER = "## runtime-input-manifest"


def parse_manifest(task: str) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """Read a manifest without binding or opening any attachment."""
    before, marker, after = str(task).partition(MANIFEST_MARKER)
    if not marker:
        return None
    start = after.find("{")
    if start < 0:
        raise ValueError("runtime-input-manifest has no JSON object")
    try:
        manifest = json.loads(after[start:])
    except (TypeError, ValueError) as error:
        raise ValueError(f"runtime-input-manifest is invalid JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise ValueError("runtime-input-manifest must be a JSON object")
    return before, after[:start], manifest


def bind_manifest(
    task: str, files: Optional[List[str]],
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """Parse a task's input manifest and bind it to the paths staging actually used.

    A launcher declares attachments by id and role while it is writing the task; staging
    happens afterwards and chooses the paths. So what the document says and where the
    bytes are only agree once something rebinds them — and it must agree exactly, which
    is why a count mismatch is refused rather than zipped short.

    Here rather than in an agent because nothing about it is one domain's: a task with
    attachments, some of which are for the run's children and not for the run, is a shape
    any orchestrator can have. What is a domain's is which roles those are.

    Returns:
        ``(text before the marker, the marker's prose, the bound manifest)``, or None
        when the task declares no manifest at all.
    """
    attachments = [str(path) for path in (files or [])]
    parsed = parse_manifest(task)
    if parsed is None:
        return None
    before, explanation, manifest = parsed
    declared = manifest.get("attachments")
    if not isinstance(declared, list):
        raise ValueError("runtime-input-manifest must contain an attachments list")
    if len(declared) != len(attachments):
        raise ValueError(
            "runtime-input-manifest attachment count does not match staged files: "
            f"declared={len(declared)}, staged={len(attachments)}"
        )
    rebound = []
    identifiers = set()
    for index, (entry, staged_path) in enumerate(zip(declared, attachments)):
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("role"):
            raise ValueError(f"runtime-input-manifest attachment {index} requires id and role")
        identifier = str(entry["id"]).strip()
        if not identifier or identifier in identifiers:
            raise ValueError(f"runtime-input-manifest attachment id is empty or duplicated: {identifier!r}")
        identifiers.add(identifier)
        bound = dict(entry)
        bound["id"] = identifier
        bound.pop("source_path", None)
        bound["path"] = staged_path
        bound["staged"] = True
        rebound.append(bound)
    manifest["attachments"] = rebound
    manifest["paths_staged"] = True
    return before.rstrip(), explanation.strip(), manifest


def render_manifest(before: str, explanation: str, manifest: Dict[str, Any]) -> str:
    """A task text with its manifest written back into it."""
    return (
        f"{before}\n\n{MANIFEST_MARKER}\n"
        f"{explanation}\n"
        f"{json.dumps(manifest, ensure_ascii=False, indent=2)}"
    )


def without_private_paths(
    manifest: Dict[str, Any], private_roles: Container[str],
) -> Dict[str, Any]:
    """A copy of ``manifest`` in which private attachments have no path to open.

    The privacy boundary itself, and one implementation of it: an agent given the path of
    a document routed to its children can read what it is being judged against, and the
    difference between "it has the path" and "it does not" is these three keys.
    """
    public = json.loads(json.dumps(manifest))
    for entry in public.get("attachments") or []:
        entry.pop("source_path", None)
        if entry.get("role") in private_roles:
            entry.pop("path", None)
            entry.pop("staged", None)
            entry["routing"] = "runtime_private"
    return public


def public_manifest(
    before: str,
    explanation: str,
    manifest: Dict[str, Any],
    *,
    private_roles: Container[str] = (),
    updates: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[str]]:
    """Project a bound task into public text and its matching attachment list.

    Role policy and public runtime bindings come from the caller. Applying both
    projections here prevents a private path hidden in JSON from still being loaded
    through the agent's file list. The original manifest stays available for routing
    private inputs; this controls model input, not filesystem access permissions.
    """
    public = dict(manifest)
    public.update(updates or {})
    public = without_private_paths(public, private_roles)
    files = [
        str(item["path"])
        for item in public.get("attachments") or []
        if item.get("role") not in private_roles and item.get("path")
    ]
    return render_manifest(before, explanation, public), files


def documents_text(paths: List[str], *, label: str) -> str:
    """Several task documents as one text, for a reader that has no file tool.

    An empty one is refused: a subscriber handed a blank persona reports having no
    context at all, which reads as a routing bug rather than as an empty file.
    """
    documents = []
    for path in paths:
        document = load_task_document(path)
        if not document.content.strip():
            raise ValueError(f"{label} attachment is empty: {path}")
        documents.append(document.content.strip())
    return "\n\n".join(documents)


def subscriber_declarations(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resolve explicitly assigned attachments into generic runtime subscriber briefs.

    All files are read before startup. The input manifest stays unmodified so expanded
    private briefs cannot accidentally be rendered back into the parent's task.
    """
    declared = manifest.get("subscribers", [])
    if not isinstance(declared, list):
        raise ValueError("subscribers must be a list")
    attachments = {item["id"]: item for item in manifest.get("attachments", [])}
    result = []
    for item in declared:
        if not isinstance(item, dict) or not isinstance(item.get("brief"), dict):
            raise ValueError("Each subscriber requires id, agent and brief")
        entry = json.loads(json.dumps(item))
        refs = entry.pop("attachments", [])
        if not isinstance(refs, list):
            raise ValueError("subscriber attachments must be a list of attachment IDs")
        paths = []
        for ref in refs:
            attachment = attachments.get(str(ref))
            if not attachment or not attachment.get("path"):
                raise ValueError(f"Subscriber {entry.get('id')!r} has no staged attachment {ref!r}")
            paths.append(str(attachment["path"]))
        if paths:
            entry["brief"]["task"] = str(entry["brief"].get("task") or "") + (
                "\n\n--- assigned task context ---\n"
                + documents_text(paths, label=f"subscriber {entry.get('id')}")
            )
        result.append(entry)
    return result
