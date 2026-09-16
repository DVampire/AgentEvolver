"""Stable structured companion for portable compaction checkpoints."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

CHECKPOINT_SCHEMA_VERSION = 1
_SOURCE = re.compile(r"\[source_seq=([^\]]+)\]")
_NUMBER = re.compile(r"\d+")
_BULLET = re.compile(r"^\s*(?:[-*+] |\d+[.)]\s+)(.*)$")

_HEADINGS: Dict[str, str] = {
    "current objective": "objective",
    "acceptance conditions": "acceptance_conditions",
    "established facts": "established_facts",
    "decisions": "decisions",
    "workspace mutations": "workspace_mutations",
    "verification": "verification",
    "failed approaches": "failed_approaches",
    "remaining conditions": "open_blockers",
    "open blockers": "open_blockers",
    "next action": "next_action",
}
_LABELS = {
    "objective": "Current objective",
    "acceptance_conditions": "Acceptance conditions",
    "established_facts": "Established facts",
    "decisions": "Decisions",
    "workspace_mutations": "Workspace mutations",
    "verification": "Verification",
    "failed_approaches": "Failed approaches",
    "open_blockers": "Open blockers",
    "next_action": "Next action",
}


class CheckpointStatement(BaseModel):
    """One model-visible fact and the Trace sequence references it retained."""

    model_config = ConfigDict(extra="forbid")

    text: str
    source_seqs: List[int] = Field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> "CheckpointStatement":
        source_seqs = sorted({
            int(number)
            for marker in _SOURCE.findall(text)
            for number in _NUMBER.findall(marker)
        })
        return cls(text=text.strip(), source_seqs=source_seqs)


class PortableCheckpoint(BaseModel):
    """Provider-neutral checkpoint schema stored beside the readable Markdown."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    objective: Optional[CheckpointStatement] = None
    acceptance_conditions: List[CheckpointStatement] = Field(default_factory=list)
    established_facts: List[CheckpointStatement] = Field(default_factory=list)
    decisions: List[CheckpointStatement] = Field(default_factory=list)
    workspace_mutations: List[CheckpointStatement] = Field(default_factory=list)
    verification: List[CheckpointStatement] = Field(default_factory=list)
    failed_approaches: List[CheckpointStatement] = Field(default_factory=list)
    open_blockers: List[CheckpointStatement] = Field(default_factory=list)
    next_action: Optional[CheckpointStatement] = None

    @classmethod
    def from_text(cls, text: str) -> "PortableCheckpoint":
        """Parse heading-based Markdown, retaining an explicit legacy fallback."""
        raw = str(text or "").strip()
        if not raw:
            return cls()
        sections: Dict[str, List[str]] = {}
        active: Optional[str] = None
        preamble: List[str] = []
        for line in raw.splitlines():
            heading = re.sub(r"^\s*#{1,6}\s*", "", line).strip().rstrip(":").lower()
            field = _HEADINGS.get(heading)
            if field:
                active = field
                sections.setdefault(field, [])
            elif active is None:
                preamble.append(line)
            else:
                sections[active].append(line)
        if not sections:
            return cls(established_facts=[CheckpointStatement.parse(raw)])
        if any(line.strip() for line in preamble):
            sections.setdefault("established_facts", []).insert(0, "\n".join(preamble))

        values: Dict[str, object] = {}
        for field, lines in sections.items():
            statements = cls._statements(lines)
            if field in {"objective", "next_action"}:
                values[field] = statements[0] if statements else None
            else:
                values[field] = statements
        return cls(**values)

    @staticmethod
    def _statements(lines: List[str]) -> List[CheckpointStatement]:
        items: List[str] = []
        paragraph: List[str] = []

        def flush() -> None:
            value = " ".join(part.strip() for part in paragraph if part.strip()).strip()
            if value:
                items.append(value)
            paragraph.clear()

        for line in lines:
            bullet = _BULLET.match(line)
            if bullet:
                flush()
                items.append(bullet.group(1).strip())
            elif line.strip():
                paragraph.append(line)
            else:
                flush()
        flush()
        return [CheckpointStatement.parse(item) for item in items if item]

    def render(self) -> str:
        """Render the canonical concise Markdown injected back into context."""
        blocks: List[str] = []
        for field in _LABELS:
            value = getattr(self, field)
            statements = [value] if isinstance(value, CheckpointStatement) else value
            if not statements:
                continue
            blocks.append(f"### {_LABELS[field]}")
            if field in {"objective", "next_action"}:
                blocks.append(statements[0].text)
            else:
                blocks.extend(f"- {statement.text}" for statement in statements)
        return "\n\n".join(blocks)


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointStatement",
    "PortableCheckpoint",
]
