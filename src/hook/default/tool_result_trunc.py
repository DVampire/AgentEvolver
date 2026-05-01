"""ToolResultTruncHook — truncates individual tool result messages that are too long.

Fires on PRE_MESSAGES. Scans all HumanMessage / tool-result messages and
truncates any content part that exceeds `max_result_tokens`. For file-read
results it applies head+tail truncation to preserve both ends.
"""

from __future__ import annotations

from typing import List

from src.logger import logger
from src.message import Message, HumanMessage
from src.message.types import ContentPartText
from src.utils import count_tokens, truncate_text
from src.hook.types import HookContext, HookEvent, HookResult, Hook

_TRUNC_MARKER = "\n...[truncated — {kept} tokens kept of {total}]\n"
_HEAD_TAIL_RATIO = 0.6


def _truncate_file_content(text: str, max_tokens: int, model: str) -> str:
    """Keep head + tail with a marker in the middle."""
    total = count_tokens(text, model=model)
    if total <= max_tokens:
        return text

    head_budget = int(max_tokens * _HEAD_TAIL_RATIO)
    tail_budget = max_tokens - head_budget

    lines = text.splitlines(keepends=True)

    head_lines, head_tokens = [], 0
    for line in lines:
        t = count_tokens(line, model=model)
        if head_tokens + t > head_budget:
            break
        head_lines.append(line)
        head_tokens += t

    tail_lines, tail_tokens = [], 0
    for line in reversed(lines):
        t = count_tokens(line, model=model)
        if tail_tokens + t > tail_budget:
            break
        tail_lines.insert(0, line)
        tail_tokens += t

    marker = _TRUNC_MARKER.format(kept=head_tokens + tail_tokens, total=total)
    return "".join(head_lines) + marker + "".join(tail_lines)


class ToolResultTruncHook(Hook):
    name: str = "tool_result_trunc"
    description: str = "Truncates individual tool result messages that exceed max_result_tokens."
    events: list = [HookEvent.PRE_MESSAGES]
    priority: int = 20

    max_result_tokens: int = 2048
    model_name: str = ""
    file_tool_names: List[str] = ["read_file_tool", "bash_tool", "python_interpreter_tool"]

    async def handle(self, ctx: HookContext) -> HookResult:
        if not ctx.messages:
            return HookResult.allow()

        modified = False
        new_messages: List[Message] = []

        for msg in ctx.messages:
            if not isinstance(msg, HumanMessage):
                new_messages.append(msg)
                continue

            content = msg.content
            if isinstance(content, str):
                total = count_tokens(content, model=self.model_name)
                if total > self.max_result_tokens:
                    truncated = _truncate_file_content(content, self.max_result_tokens, self.model_name)
                    logger.debug(f"| ✂️  Truncated str message {total}→{self.max_result_tokens} tokens")
                    new_messages.append(HumanMessage(content=truncated))
                    modified = True
                else:
                    new_messages.append(msg)
                continue

            if not isinstance(content, list):
                new_messages.append(msg)
                continue

            new_parts = []
            msg_modified = False
            for part in content:
                if not isinstance(part, ContentPartText):
                    new_parts.append(part)
                    continue
                total = count_tokens(part.text, model=self.model_name)
                if total <= self.max_result_tokens:
                    new_parts.append(part)
                    continue
                truncated = _truncate_file_content(part.text, self.max_result_tokens, self.model_name)
                logger.debug(f"| ✂️  Truncated content part {total}→{self.max_result_tokens} tokens")
                new_parts.append(ContentPartText(text=truncated))
                msg_modified = True

            if msg_modified:
                new_messages.append(HumanMessage(content=new_parts))
                modified = True
            else:
                new_messages.append(msg)

        if modified:
            return HookResult.modify_messages(new_messages)
        return HookResult.allow()
