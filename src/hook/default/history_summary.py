"""HistorySummaryHook — compresses agent history when total tokens exceed threshold.

Fires on PRE_MESSAGES. Strategy:
  1. Keep system message (index 0) and the last `keep_recent_messages` messages intact.
  2. If total tokens exceed `trigger_ratio * max_tokens` AND there are more compactable
     messages than `keep_recent_messages`, summarise the middle history with an LLM call.
  3. The split point is walked back to never cut a ToolCall/ToolResult pair.
  4. On re-compaction the old and new summaries are merged (not overwritten).
  5. The summarised history is replaced by a single HumanMessage containing the structured
     summary wrapped in a direct-resume instruction so the agent continues without preamble.

Concurrency: per-session lock ensures only one summary LLM call runs at a
time per session.
"""

from __future__ import annotations

import re
from typing import List, Optional, Set
from src.registry import HOOK

from src.logger import logger
from src.message import Message, SystemMessage, HumanMessage, AssistantMessage
from src.message.types import ContentPartText
from src.model import model_manager
from src.utils import count_message_tokens
from src.hook.types import HookContext, HookEvent, HookResult, Hook
from src.hook.context import HookSessionState

_COMPACT_PREAMBLE = (
    "This session is being continued from a previous conversation that ran out of context. "
    "The summary below covers the earlier portion of the conversation."
)

_DIRECT_RESUME_INSTRUCTION = (
    "Continue the conversation from where it left off without asking the user any further "
    "questions. Resume directly — do not acknowledge the summary, do not recap what was "
    "happening, do not preface with \"I'll continue\" or similar. Pick up the last task "
    "as if the break never happened."
)

_SUMMARY_SYSTEM_PROMPT = """\
You are a concise summariser for an AI agent's execution history.
Given a sequence of agent steps (thoughts, actions, and results), produce a \
structured summary that preserves all information the agent needs to continue \
working effectively.

Output format (markdown):
## Progress Summary
### Completed steps
- bullet per completed action, include key facts/values discovered
### Current state
- Files modified: list or "none"
- Last action result: one sentence
### Key findings
- important facts, error messages, or constraints discovered
### Remaining work (if known)
- what the agent said it still needs to do

Be concise. Omit filler. Preserve exact values (file paths, numbers, errors).\
"""

_PENDING_RE = re.compile(
    r"\b(todo|next|pending|follow[- ]up|remaining|still need|need to|should|will)\b",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(r"[^\s\"'<>(){}\[\]]+/[^\s\"'<>(){}\[\]]+\.[a-zA-Z0-9]{1,10}")


def _extract_pre_context(messages: List[Message]) -> str:
    """Extract structured facts from messages before sending to the LLM."""
    tool_names: Set[str] = set()
    recent_user_texts: List[str] = []
    pending_lines: List[str] = []
    file_paths: Set[str] = set()

    for m in messages:
        if isinstance(m, AssistantMessage):
            for tc in m.tool_calls:
                tool_names.add(tc.function.name)
        text = m.text if hasattr(m, "text") else (m.content if isinstance(m.content, str) else "")
        for match in _FILE_PATH_RE.finditer(text):
            file_paths.add(match.group(0))
        for line in text.splitlines():
            if _PENDING_RE.search(line):
                stripped = line.strip()
                if stripped and stripped not in pending_lines:
                    pending_lines.append(stripped)

    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            txt = m.text.strip()
            if txt:
                recent_user_texts.append(txt)
            if len(recent_user_texts) == 3:
                break
    recent_user_texts.reverse()

    parts = []
    if tool_names:
        parts.append(f"Tools used: {', '.join(sorted(tool_names))}")
    if recent_user_texts:
        parts.append("Recent user requests:\n" + "\n".join(f"  - {t[:200]}" for t in recent_user_texts))
    if file_paths:
        parts.append(f"Key files mentioned: {', '.join(sorted(file_paths)[:20])}")
    if pending_lines:
        parts.append("Pending/upcoming work mentions:\n" + "\n".join(f"  - {l[:200]}" for l in pending_lines[:10]))
    return "\n\n".join(parts)


def _messages_to_text(messages: List[Message]) -> str:
    parts = []
    for m in messages:
        role = type(m).__name__.replace("Message", "").lower()
        text = m.text if hasattr(m, "text") else (m.content if isinstance(m.content, str) else "")
        if isinstance(m, AssistantMessage) and m.tool_calls:
            tool_str = ", ".join(f"{tc.function.name}()" for tc in m.tool_calls)
            text = f"{text}\n[Tool calls: {tool_str}]"
        parts.append(f"[{role}]\n{text}")
    return "\n\n---\n\n".join(parts)


@HOOK.register_module(force=True)
class HistorySummaryHook(Hook):
    name: str = "history_summary"
    description: str = "Summarises old agent history when total tokens exceed the threshold."
    events: list = [HookEvent.PRE_MESSAGES]
    priority: int = 50

    trigger_ratio: float = 0.75
    keep_recent_messages: int = 6
    summary_model: str = ""

    async def handle(self, ctx: HookContext) -> HookResult:
        if not ctx.messages or not ctx.max_tokens:
            return HookResult.allow()

        state: HookSessionState = ctx.extra.get("_session_state")
        if state is None:
            return HookResult.allow()

        threshold = int(ctx.max_tokens * self.trigger_ratio)

        current_tokens = state.last_token_count
        if current_tokens == 0:
            msg_dicts = self._to_dicts(ctx.messages)
            current_tokens = count_message_tokens(msg_dicts)

        # Dual-condition trigger: token count AND enough compactable messages
        system_msg = ctx.messages[0] if ctx.messages and isinstance(ctx.messages[0], SystemMessage) else None
        body = ctx.messages[1:] if system_msg else ctx.messages
        compactable_count = max(0, len(body) - self.keep_recent_messages)

        if current_tokens <= threshold or compactable_count == 0:
            return HookResult.allow()

        logger.info(
            f"| 📝 [{ctx.id}] History summary triggered "
            f"({current_tokens} > {threshold} tokens, {compactable_count} compactable messages)"
        )

        async with state.get_lock():
            if state.last_token_count <= threshold:
                return HookResult.allow()

            new_messages = await self._summarise(ctx.messages, state, ctx.id)
            if new_messages is None:
                return HookResult.allow()

            new_dicts = self._to_dicts(new_messages)
            state.last_token_count = count_message_tokens(new_dicts)

        # Post-compaction health probe: verify tool system is still responsive
        await self._health_probe(ctx.id)

        additional = f"[Context was compressed. Summary covers steps 1–{state.summary_covers_steps}.]"
        return HookResult.modify_messages(new_messages, additional_context=additional)

    async def _summarise(
        self,
        messages: List[Message],
        state: HookSessionState,
        session_id: str,
    ) -> Optional[List[Message]]:
        n = len(messages)
        if n <= self.keep_recent_messages + 1:
            return None

        system_msg = messages[0] if isinstance(messages[0], SystemMessage) else None
        body = messages[1:] if system_msg else messages

        split_point = max(0, len(body) - self.keep_recent_messages)

        # ToolCall/ToolResult boundary protection: walk back split_point so we never
        # summarise an AssistantMessage with tool_calls while leaving its result in recent_tail.
        while split_point > 0:
            prev = body[split_point - 1]
            if isinstance(prev, AssistantMessage) and prev.tool_calls:
                split_point -= 1
            else:
                break

        to_summarise = body[:split_point]
        recent_tail = body[split_point:]

        if not to_summarise:
            return None

        if state.summary_text and state.summary_covers_steps >= split_point:
            summary_text = state.summary_text
        else:
            new_summary = await self._call_llm(to_summarise, session_id)
            if new_summary is None:
                return None

            # Multi-round summary merging: preserve old summary instead of overwriting
            if state.summary_text:
                summary_text = (
                    "### Previously compacted context\n\n"
                    f"{state.summary_text}\n\n"
                    "---\n\n"
                    "### Newly compacted context\n\n"
                    f"{new_summary}"
                )
            else:
                summary_text = new_summary

            state.summary_text = summary_text
            state.summary_covers_steps = split_point

        # Wrap in preamble + direct resume instruction (claw-code compact.rs pattern)
        continuation_text = (
            f"{_COMPACT_PREAMBLE}\n\n"
            f"### Compressed History\n\n"
            f"{summary_text}\n\n"
            "---\n\n"
            f"{_DIRECT_RESUME_INSTRUCTION}"
        )
        summary_msg = HumanMessage(
            content=[ContentPartText(text=continuation_text)]
        )

        result: List[Message] = []
        if system_msg:
            result.append(system_msg)
        result.append(summary_msg)
        result.extend(recent_tail)
        return result

    async def _call_llm(self, messages: List[Message], session_id: str) -> Optional[str]:
        # Structured pre-extraction gives the LLM grounding before it reads the full history
        pre_context = _extract_pre_context(messages)
        history_text = _messages_to_text(messages)

        prompt_parts = ["Summarise this agent history:"]
        if pre_context:
            prompt_parts.append(f"\n## Pre-extracted facts\n{pre_context}")
        prompt_parts.append(f"\n## Full history\n{history_text}")

        try:
            response = await model_manager(
                model=self.summary_model,
                messages=[
                    SystemMessage(content=_SUMMARY_SYSTEM_PROMPT),
                    HumanMessage(content="\n\n".join(prompt_parts)),
                ],
            )
            logger.info(f"| ✅ [{session_id}] History summarised ({len(response.message)} chars)")
            return response.message
        except Exception as e:
            logger.warning(f"| ⚠️ History summary LLM call failed: {e}")
            return None

    async def _health_probe(self, session_id: str) -> None:
        """Non-destructive post-compaction probe: verify the tool system responds.

        Ported from claw-code conversation.rs health-probe pattern.
        Failure is non-blocking — we log a warning but never abort the compaction.
        """
        try:
            from src.tool.server import tool_manager
            tool = tool_manager.get("glob_search_tool")
            if tool is None:
                logger.debug(f"| 🔍 [{session_id}] Health probe skipped (glob_search_tool not registered)")
                return
            result = await tool(pattern="*.py", directory=".")
            if result.success:
                logger.debug(f"| 🔍 [{session_id}] Post-compaction health probe OK")
            else:
                logger.warning(
                    f"| ⚠️ [{session_id}] Post-compaction health probe returned failure: {result.message}"
                )
        except Exception as e:
            logger.debug(f"| 🔍 [{session_id}] Post-compaction health probe skipped: {e}")

    @staticmethod
    def _to_dicts(messages: List[Message]) -> list:
        result = []
        for m in messages:
            if hasattr(m, "model_dump"):
                d = m.model_dump()
                if isinstance(d.get("content"), list):
                    parts = d["content"]
                    d["content"] = "\n".join(
                        p.get("text", "") for p in parts
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                result.append(d)
        return result
