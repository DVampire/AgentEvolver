"""Durable, evidence-only project memory shared across sessions."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from agentevolver.paths import P, path_manager
from agentevolver.utils.file_utils import atomic_json_update

_MAX_ENTRIES_PER_SECTION = 100
_SECRET = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|password|secret|authorization)\s*[:=]"
)


def _identity(workspace_root: str, source_workspace: Optional[str] = None) -> str:
    source = Path(source_workspace or workspace_root).expanduser().resolve()
    return hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:20]


def project_memory_path(
    workspace_root: str, source_workspace: Optional[str] = None,
) -> Optional[Path]:
    """Resolve the owner/project memory file without placing state in the checkout."""
    bound = path_manager.session
    if bound is None or not workspace_root:
        return None
    owner, _ = bound
    return path_manager.get(
        P.OWNER_PROJECT_MEMORY,
        owner=owner,
        project_key=_identity(workspace_root, source_workspace),
    )


class ProjectMemoryStore:
    """Small structured store; entries must cite runtime evidence or explicit input."""

    def __init__(self, workspace_root: str, source_workspace: Optional[str] = None):
        self.workspace_root = str(workspace_root or "")
        self.source_workspace = str(source_workspace or "") or None
        self.path = project_memory_path(self.workspace_root, self.source_workspace)

    def remember(
        self, section: str, text: str, *, source: str, confidence: str = "high",
    ) -> bool:
        """Append a deduplicated, bounded fact. Sensitive-looking text is refused."""
        if self.path is None:
            return False
        section = re.sub(r"[^a-z0-9_]+", "_", str(section).lower()).strip("_")
        value = " ".join(str(text or "").strip().split())
        if not section or not value or _SECRET.search(value):
            return False
        digest = hashlib.sha256(f"{section}\0{value}".encode("utf-8")).hexdigest()

        changed = False

        def update(current: Any) -> Dict[str, Any]:
            nonlocal changed
            document = dict(current or {})
            document.setdefault("version", 1)
            sections = document.setdefault("sections", {})
            entries = list(sections.get(section) or [])
            if any(item.get("digest") == digest for item in entries if isinstance(item, dict)):
                return document
            entries.append({
                "text": value,
                "source": str(source),
                "confidence": str(confidence),
                "digest": digest,
            })
            sections[section] = entries[-_MAX_ENTRIES_PER_SECTION:]
            changed = True
            return document

        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_update(self.path, update, default={})
        return changed

    def render(self, *, max_chars: int = 12_000) -> str:
        """Render stable Markdown for the fixed project-context layer."""
        if self.path is None or not self.path.is_file():
            return ""
        import json

        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        lines = ["Evidence-derived project memory; every item cites its source Trace."]
        omitted = 0
        for section, entries in (document.get("sections") or {}).items():
            if not entries:
                continue
            lines.append(f"\n#### {str(section).replace('_', ' ').title()}")
            for item in entries:
                if not isinstance(item, dict) or not item.get("text"):
                    continue
                line = f"- {item['text']}  [source: {item.get('source', 'unknown')}]"
                if sum(len(value) + 1 for value in [*lines, line]) > max_chars:
                    omitted += 1
                    continue
                lines.append(line)
        if omitted:
            lines.append(
                f"\n[{omitted} complete auto-memory entries omitted from this fixed "
                "context; their exact values remain in the project memory store]"
            )
        return "\n".join(lines) if len(lines) > 1 else ""

    def learn_trace(self, events: Iterable[Any], *, session_id: str) -> int:
        """Learn only reproducible commands and recurring failures from settled Trace."""
        starts: Dict[tuple, Any] = {}
        failures: Dict[str, tuple[int, Any]] = {}
        learned = 0
        for event in events:
            key = (getattr(event, "step_number", None), getattr(event, "action_index", None))
            event_type = str(getattr(getattr(event, "event_type", None), "value", ""))
            if event_type == "tool_start":
                starts[key] = event
                continue
            if event_type != "tool_call":
                continue
            start = starts.get(key)
            action = str(getattr(event, "action_name", "") or "")
            if getattr(event, "success", None) is True and action == "bash_tool" and start:
                command = str((getattr(start, "input", None) or {}).get("command") or "")
                if command and any(token in command.lower() for token in (
                    "pytest", "ruff", "mypy", "npm test", "pnpm test", "npm run build",
                    "pnpm build", "cargo test", "go test", "make test",
                )):
                    learned += int(self.remember(
                        "verified_commands",
                        command,
                        source=f"trace:{session_id}:{getattr(event, 'seq_no', '?')}",
                    ))
            if getattr(event, "success", None) is False:
                raw = str(getattr(event, "error", None) or getattr(event, "message", None) or "")
                signature = " ".join(raw.split())
                if signature:
                    count, first = failures.get(signature, (0, event))
                    failures[signature] = (count + 1, first)
        for signature, (count, event) in failures.items():
            if count < 2:
                continue
            learned += int(self.remember(
                "recurring_failures",
                f"{getattr(event, 'action_name', 'action')}: {signature}",
                source=f"trace:{session_id}:{getattr(event, 'seq_no', '?')}",
            ))
        return learned


__all__ = ["ProjectMemoryStore", "project_memory_path"]
