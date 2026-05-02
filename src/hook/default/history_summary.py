"""HistorySummaryHook — compresses agent history when total tokens exceed threshold.

Fires on PRE_MESSAGES. Strategy:
  1. Keep system message (index 0) and the last `keep_recent_messages` messages intact.
  2. If total tokens exceed `trigger_ratio * max_tokens`, summarise the middle
     history with an LLM call.
  3. The summary is cached in session state; it is reused until new steps are added.
  4. The summarised history is replaced by a single HumanMessage containing
     the structured summary.

Concurrency: per-session lock ensures only one summary LLM call runs at a
time per session.
"""

from __future__ import annotations

from typing import List, Optional
from src.registry import HOOK

from src.logger import logger
from src.message import Message, SystemMessage, HumanMessage
from src.message.types import ContentPartText
from src.model import model_manager
from src.utils import count_message_tokens
from src.hook.types import HookContext, HookEvent, HookResult, Hook
from src.hook.context import HookSessionState

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


def _messages_to_text(messages: List[Message]) -> str:
    parts = []
    for m in messages:
        role = type(m).__name__.replace("Message", "").lower()
        content = m.content if isinstance(m.content, str) else m.text
        parts.append(f"[{role}]\n{content}")
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

        if current_tokens <= threshold:
            return HookResult.allow()

        logger.info(
            f"| 📝 [{ctx.id}] History summary triggered "
            f"({current_tokens} > {threshold} tokens)"
        )

        async with state.get_lock():
            if state.last_token_count <= threshold:
                return HookResult.allow()

            new_messages = await self._summarise(ctx.messages, state, ctx.id)
            if new_messages is None:
                return HookResult.allow()

            new_dicts = self._to_dicts(new_messages)
            state.last_token_count = count_message_tokens(new_dicts)

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
        to_summarise = body[:split_point]
        recent_tail = body[split_point:]

        if not to_summarise:
            return None

        if state.summary_text and state.summary_covers_steps >= split_point:
            summary_text = state.summary_text
        else:
            summary_text = await self._call_llm(to_summarise, session_id)
            if summary_text is None:
                return None
            state.summary_text = summary_text
            state.summary_covers_steps = split_point

        summary_msg = HumanMessage(
            content=[ContentPartText(text=f"### Compressed History\n\n{summary_text}")]
        )

        result: List[Message] = []
        if system_msg:
            result.append(system_msg)
        result.append(summary_msg)
        result.extend(recent_tail)
        return result

    async def _call_llm(self, messages: List[Message], session_id: str) -> Optional[str]:
        history_text = _messages_to_text(messages)
        try:
            response = await model_manager(
                model=self.summary_model,
                messages=[
                    SystemMessage(content=_SUMMARY_SYSTEM_PROMPT),
                    HumanMessage(content=f"Summarise this agent history:\n\n{history_text}"),
                ],
            )
            logger.info(f"| ✅ [{session_id}] History summarised ({len(response.message)} chars)")
            return response.message
        except Exception as e:
            logger.warning(f"| ⚠️ History summary LLM call failed: {e}")
            return None

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
