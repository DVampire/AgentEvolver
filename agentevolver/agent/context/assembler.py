"""ContextAssembler: the one place that decides what the model sees.

Agents ask for a context. They do not build one, do not decide where a block goes, and
do not compact. That boundary is the whole reason this class exists: prompt layout was
previously spread across twenty-one methods on the agent base class, so every actor that
needed one different block overrode an assembly method and quietly acquired a different
layout.

Cache placement is the other reason. A provider's prompt cache is a *prefix* match, so
where a block sits decides whether a session pays for its history again every step.

    fixed        system + task anchor. Written once, breakpoint after it.
    checkpoint   the folded summary. When it carries a *native* Anthropic compaction
                 block, that block becomes the prefix boundary, so the anchor's
                 breakpoint moves here instead of being kept as well.
    recent       exact turns. Breakpoint on the last assistant message of
                 checkpoint+recent, so a turn already sent is never re-tokenised.
    live         this step's volatile state. Never cached, always last — anything
                 volatile placed earlier invalidates everything behind it.

These rules are not new: they reproduce
:class:`~agentevolver.agent.context.builder.ContextBuilder`, which derived the same
envelope from a persisted trace. Only the source of the history changed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from agentevolver.agent.context.conversation import Conversation
from agentevolver.agent.context.envelope import ContextEnvelope
from agentevolver.agent.context.layers import ContextMessages
from agentevolver.agent.context.sanitize import strip_rendered_comments
from agentevolver.logger import logger
from agentevolver.message.types import AssistantMessage, HumanMessage, Message

#: Turns kept verbatim when history is folded. Fewer loses the thread of what was just
#: tried; more defeats the point of folding at all.
DEFAULT_RETAIN_TURNS = 4

#: Check for worthwhile history compaction after this many complete turns.
DEFAULT_COMPACT_AFTER_TURNS = 24

#: Tokens of live body — everything after the checkpoint — past which history is folded.
DEFAULT_COMPACT_BODY_TOKENS = 100_000

#: Fraction of the model's window at which folding becomes a capacity matter.
DEFAULT_FOLD_AT_PRESSURE = 0.85

#: Folds allowed in one run. A history that cannot shrink further would otherwise be
#: asked once per step for the rest of the budget, producing the same request each time.
DEFAULT_MAX_FOLDS = 32

#: Ceiling for a checkpoint's own size. A summariser that writes more than this has
#: expanded rather than compressed, and its output is refused.
DEFAULT_COMPACT_OUTPUT_TOKENS = 2048

#: Assumed window when a route does not state one. Guess high: too low is a wall we
#: invented, while too high is a wall the provider states and can be recovered from.
DEFAULT_CONTEXT_WINDOW = 1_000_000


class ContextAssembler:
    """Builds the four-layer request, and decides when history must be folded."""

    def __init__(
        self,
        *,
        retain_turns: int = DEFAULT_RETAIN_TURNS,
        compact_after_turns: int = DEFAULT_COMPACT_AFTER_TURNS,
        compact_body_tokens: int = DEFAULT_COMPACT_BODY_TOKENS,
        fold_at_pressure: float = DEFAULT_FOLD_AT_PRESSURE,
        max_folds: int = DEFAULT_MAX_FOLDS,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        compact_output_tokens: int = DEFAULT_COMPACT_OUTPUT_TOKENS,
    ) -> None:
        self.retain_turns = max(1, int(retain_turns))
        self.compact_after_turns = int(compact_after_turns)
        self.compact_body_tokens = int(compact_body_tokens)
        self.fold_at_pressure = float(fold_at_pressure)
        self.max_folds = int(max_folds)
        self.context_window = int(context_window)
        self.compact_output_tokens = int(compact_output_tokens)

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def build(
        self,
        conversation: Conversation,
        *,
        live: Sequence[str] = (),
        attachments: Sequence[Message] = (),
    ) -> ContextMessages:
        """The messages to send this step, flattened and layer-tagged."""
        return self.build_envelope(
            conversation, live=live, attachments=attachments
        ).flatten()

    def build_envelope(
        self,
        conversation: Conversation,
        *,
        live: Sequence[str] = (),
        attachments: Sequence[Message] = (),
    ) -> ContextEnvelope:
        """The validated envelope. Raises if the conversation is not sendable.

        ``attachments`` are whole messages rather than text — images a tool read during
        the run. They ride in the live layer and are re-sent every step: the request is
        rebuilt each time, so an image appended once is absent from the next one, and
        re-attaching is what makes "look at this screenshot" hold for more than a turn.
        """
        fixed = self._fixed(conversation)
        checkpoint = tuple(
            [conversation.checkpoint.model_copy()]
            if conversation.checkpoint is not None else []
        )
        recent = self._recent(conversation)
        live_messages = [*self._live(live), *attachments]

        self._place_breakpoints(fixed, checkpoint, recent)
        return ContextEnvelope(
            fixed=tuple(fixed),
            checkpoint=checkpoint,
            recent=tuple(recent),
            live=tuple(live_messages),
        ).validate()

    def _fixed(self, conversation: Conversation) -> List[Message]:
        """System instructions, then the task anchor. Never rewritten after step 0."""
        messages = list(strip_rendered_comments(list(conversation.system)))
        if conversation.task:
            messages.append(
                HumanMessage(content=f"<task>\n{conversation.task}\n</task>")
            )
        return messages

    @staticmethod
    def _recent(conversation: Conversation) -> List[Message]:
        """The exact tail. Copied so breakpoint marking cannot mutate stored state."""
        return [message.model_copy() for message in conversation.items]

    @staticmethod
    def _live(blocks: Sequence[str]) -> List[Message]:
        """This step's volatile state, as at most one user turn.

        One message rather than several: each is a cache miss by construction, and a
        single block is also how the model reads them — as the current situation, not as
        a series of separate remarks.
        """
        body = "\n\n".join(block.strip() for block in blocks if block and block.strip())
        return [HumanMessage(content=body)] if body else []

    @staticmethod
    def _place_breakpoints(
        fixed: List[Message],
        checkpoint: Sequence[Message],
        recent: List[Message],
    ) -> None:
        """Mark where the provider may cut a cache prefix.

        Reproduces ``ContextBuilder.build_envelope``. The Anthropic branch is the part
        that is easy to get wrong: a native ``compact_20260112`` block *is* the new
        prefix, so keeping the anchor's breakpoint as well would ask the provider to
        cache a prefix that its own compaction has already replaced.
        """
        for message in (*fixed, *checkpoint, *recent):
            message.cache = False

        if fixed:
            fixed[-1].cache = True

        if checkpoint:
            native = (getattr(checkpoint[-1], "provider_state", None) or {})
            anthropic = native.get("anthropic") or {}
            if anthropic.get("compaction_blocks"):
                if fixed:
                    fixed[-1].cache = False
                checkpoint[-1].cache = True

        frozen: List[Message] = [*checkpoint, *recent]
        if frozen:
            boundary = next(
                (
                    message for message in reversed(frozen)
                    if isinstance(message, AssistantMessage)
                ),
                frozen[-1],
            )
            boundary.cache = True

    # ------------------------------------------------------------------
    # Folding
    # ------------------------------------------------------------------

    def compaction_policy(self) -> Dict[str, Any]:
        """The policy the model layer needs to negotiate its own compaction.

        Passed on every request. ``model_manager`` records it in the request snapshot and
        uses it to decide whether a route's native compaction applies — without it the
        model layer falls back to defaults and native compaction never engages.
        """
        return {
            "retain_recent_steps": self.retain_turns,
            "compact_after_steps": self.compact_after_turns,
            "compact_body_tokens": self.compact_body_tokens,
            "fold_at_pressure": self.fold_at_pressure,
        }

    def estimate(self, conversation: Conversation, *, live: Sequence[str] = (),
                 attachments: Sequence[Message] = ()) -> int:
        """Rough token count for the request as it would be sent."""
        from agentevolver.model.pressure import estimate_tokens

        try:
            return int(estimate_tokens(list(self.build(conversation, live=live, attachments=attachments))))
        except Exception as error:  # noqa: BLE001 - accounting must not break a step
            logger.debug(f"| ⚙️ context estimate unavailable: {error}")
            return 0

    def body_tokens(self, conversation: Conversation) -> int:
        """Tokens of everything after the checkpoint.

        The active body, which is what folding actually removes. Measured apart from the
        whole request because the fixed layer and the checkpoint are the parts folding
        cannot shrink, so counting them would let a large stable prefix trigger folds
        that free nothing.
        """
        from agentevolver.model.pressure import estimate_tokens

        try:
            return int(estimate_tokens(list(conversation.items))) if conversation.items else 0
        except Exception as error:  # noqa: BLE001
            logger.debug(f"| ⚙️ body estimate unavailable: {error}")
            return 0

    def pressure(self, conversation: Conversation, *, live: Sequence[str] = (),
                 attachments: Sequence[Message] = ()) -> float:
        """Estimated fraction of the context window this request would occupy."""
        if self.context_window <= 0:
            return 0.0
        return self.estimate(conversation, live=live, attachments=attachments) / self.context_window

    def fold_reason(
        self, conversation: Conversation, *, live: Sequence[str] = (), folds: int = 0,
        attachments: Sequence[Message] = (),
        request_pressure: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Why history should be folded now, or "" for not yet.

        A turn-count trigger also needs enough removable history to justify a summary
        call and rewriting the cached prefix. The 4x summary budget is a conservative
        size heuristic, not a provider pricing/break-even calculation. Body and capacity
        limits remain independent safety triggers, regardless of cache reuse.
        """
        if not conversation.complete:
            # Mid-turn: folding across an unanswered tool call would sever the call from
            # its result, the one shape a provider rejects outright.
            return ""
        if folds >= self.max_folds:
            return ""
        if conversation.turns <= self.retain_turns:
            return ""

        reasons: List[str] = []
        if self.compact_after_turns and conversation.turns >= self.compact_after_turns:
            from agentevolver.model.pressure import estimate_tokens

            source_tokens = estimate_tokens(self.summarize_source(conversation))
            if source_tokens >= 4 * max(1, self.compact_output_tokens):
                reasons.append(f"history={conversation.turns} turns, removable≈{source_tokens:,} tokens")
        if self.compact_body_tokens:
            body = self.body_tokens(conversation)
            if body >= self.compact_body_tokens:
                reasons.append(f"body≈{body:,} tokens")
        if self.fold_at_pressure:
            ratio = (float(request_pressure["pressure_ratio_after"])
                     if request_pressure is not None else
                     self.pressure(conversation, live=live, attachments=attachments))
            if ratio >= self.fold_at_pressure:
                reasons.append(f"capacity={ratio:.0%}")
        return ", ".join(reasons)

    def should_fold(
        self, conversation: Conversation, *, live: Sequence[str] = (), folds: int = 0
    ) -> bool:
        """Whether history has grown enough to be worth folding."""
        return bool(self.fold_reason(conversation, live=live, folds=folds))

    def summarize_source(self, conversation: Conversation) -> List[Message]:
        """The messages a fold would remove — what to summarise, or to hand a provider."""
        return conversation.foldable(self.retain_turns)

    def valid_checkpoint(
        self, text: str, source: Sequence[Message], existing: str = ""
    ) -> Tuple[bool, str]:
        """Whether a checkpoint may replace the history it summarises.

        A summariser can expand rather than compress, and the result would be committed
        over canonical turns that cannot be recovered. Three refusals: empty, over the
        output budget, and — the one that matters — no token saving at all, which means
        the fold would cost a model call and leave the request no smaller.

        Returns ``(ok, reason)``; the reason is for the log, not the model.
        """
        from agentevolver.model.pressure import estimate_tokens

        if not text.strip():
            return False, "empty"
        after = int(estimate_tokens([HumanMessage(content=text)]))
        if after > self.compact_output_tokens:
            return False, f"output-limit:{after}>{self.compact_output_tokens}"
        before = int(estimate_tokens(
            [*(([HumanMessage(content=existing)]) if existing else []), *source]
        ))
        if after >= before:
            return False, f"no-token-saving:{before}->{after}"
        return True, "ok"

    def fold(
        self,
        conversation: Conversation,
        summary: str,
        *,
        provider_state: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Fold everything but the retained tail into one checkpoint.

        The checkpoint's content is supplied by the caller — writing a summary needs a
        model, and this class does not own one. ``provider_state`` carries a native
        checkpoint when the route produced one, and is trusted without the size check:
        the provider replaced the history itself, so its own item is the saving.
        """
        if not provider_state:
            source = self.summarize_source(conversation)
            existing = conversation.checkpoint.text if conversation.checkpoint else ""
            ok, reason = self.valid_checkpoint(summary, source, existing)
            if not ok:
                logger.warning(f"| ⚠️ rejected compaction checkpoint ({reason})")
                return 0
        folded = conversation.fold(
            summary, self.retain_turns, provider_state=provider_state
        )
        if folded:
            native = "native " if provider_state else ""
            logger.info(
                f"| 🗜️ folded {folded} message(s) into a {native}checkpoint; keeping "
                f"the last {self.retain_turns} turn(s)"
            )
        return folded

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ContextAssembler(retain_turns={self.retain_turns}, "
            f"compact_after_turns={self.compact_after_turns}, "
            f"compact_body_tokens={self.compact_body_tokens}, "
            f"fold_at_pressure={self.fold_at_pressure}, window={self.context_window})"
        )


#: Shared default. Agents that need different budgets construct their own.
context_assembler = ContextAssembler()


__all__ = [
    "DEFAULT_COMPACT_AFTER_TURNS",
    "DEFAULT_COMPACT_OUTPUT_TOKENS",
    "DEFAULT_COMPACT_BODY_TOKENS",
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_FOLD_AT_PRESSURE",
    "DEFAULT_MAX_FOLDS",
    "DEFAULT_RETAIN_TURNS",
    "ContextAssembler",
    "context_assembler",
]
