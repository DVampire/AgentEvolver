"""Strip author-only template documentation before a prompt reaches a model.

Templates carry HTML comments explaining themselves to whoever edits them. Those are
tokens the model pays for and can misread as instructions. They are removed here - but
never inside a data block, where an identical-looking comment may be part of the
material the agent was handed.
"""

from __future__ import annotations

import re
from typing import List

from agentevolver.message.types import Message

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_DATA_BLOCKS = ("task", "inherited-context", "memory", "working-memory", "recent-steps")


def _strip_template_comments(message: Message) -> Message:
    """Remove template comments without touching task or memory payloads."""
    content = getattr(message, "content", None)
    if not isinstance(content, str) or "<!--" not in content:
        return message
    if getattr(message, "role", "") == "system":
        return message.model_copy(update={"content": _HTML_COMMENT.sub("", content)})

    protected: List[str] = []

    def hold(match: re.Match) -> str:
        protected.append(match.group(0))
        return f"\x00AGENTEVOLVER_DATA_{len(protected) - 1}\x00"

    names = "|".join(re.escape(name) for name in _DATA_BLOCKS)
    cleaned = re.sub(rf"<({names})>.*?</\1>", hold, content, flags=re.S)
    cleaned = _HTML_COMMENT.sub("", cleaned)
    for index, value in enumerate(protected):
        cleaned = cleaned.replace(f"\x00AGENTEVOLVER_DATA_{index}\x00", value)
    return message.model_copy(update={"content": cleaned})


def strip_rendered_comments(rendered: List[Message]) -> List[Message]:
    """Strip author-only template documentation from rendered messages."""
    cleaned = [_strip_template_comments(message) for message in rendered]
    return rendered if all(a is b for a, b in zip(cleaned, rendered)) else cleaned


__all__ = ["strip_rendered_comments"]
