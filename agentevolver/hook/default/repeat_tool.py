"""Advisory loop-breaker: notice a call repeating verbatim, and say so.

This hook does not veto anything. It watches the chain of proposed actions, counts
runs of consecutive identical calls, and at rising run lengths returns an escalating
reminder telling the model to re-read the last result and either change approach or
finish. Whether to retry differently, gather more evidence, or stop stays with the
model; a legitimately repeated call is delayed by nothing and blocked by nothing.

Why advisory. The predecessor blocked, and to decide *what* to block it classified
tools by substring — a name containing ``poll`` or ``watch`` was exempt, everything
else was guardable. Both halves were wrong in the same way: a name is not a
behaviour, so the classification mislabelled honest tools, and a block acts on that
mislabel irreversibly. Repetition is the one thing that can be established without
interpreting anything — the model asked for the identical call twice — so that is
all this hook claims, and the strongest action it takes on the claim is to speak.

Detecting *absence of progress* is a different and much harder question, which this
hook deliberately does not attempt: no fingerprint approximates it, since a turn can
change a file and accomplish nothing, or change nothing and settle a question. That
judgement stays where the run can see deliverables — the idle-turn backstop in
``Agent._prepare_round``.

The hook is stateless. The chain lives on ``_AgentRun`` so concurrent sessions cannot
trip one another; this hook is handed the previous chain and returns the next one.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from agentevolver.hook.types import Hook, HookContext, HookEvent, HookResult
from agentevolver.registry import HOOK

#: Run lengths that speak. The first is a short nudge; every later one names the tool,
#: the run length, and the arguments. Ascending, and each at least 2 — a "run" of one
#: is just a call.
THRESHOLDS: Tuple[int, ...] = (3, 5, 8)

#: Tools that neither advance nor reset the chain. Bookkeeping the model interleaves
#: into a loop must not launder it: with `todo_tool` transparent,
#: ``grep X → todo_tool → grep X`` is still two consecutive ``grep X``.
TRANSPARENT = frozenset({"todo_tool", "journal_tool", "inspect_tool", "reply_tool"})

#: Cap on arguments quoted back in a reminder. The chain key always compares the FULL
#: signature; this bounds only what is repeated into the prompt, so a looping `write`
#: carrying a large payload cannot ride into the next request unbounded.
ARGS_PREVIEW_CHARS = 500


def _tracked(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The actions that participate in the chain."""
    return [a for a in actions if str(a.get("name") or "") not in TRANSPARENT]


def _preview(signature: str) -> str:
    """The call's arguments, bounded, with what was dropped stated."""
    try:
        args = json.loads(signature).get("args", {})
        text = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (ValueError, AttributeError):
        text = signature
    if len(text) <= ARGS_PREVIEW_CHARS:
        return text
    return f"{text[:ARGS_PREVIEW_CHARS]} […{len(text) - ARGS_PREVIEW_CHARS:,} more characters]"


def advance_chain(
    chain: Optional[Dict[str, Any]], actions: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Fold one proposed batch into the chain. Pure; the caller owns the state.

    A batch counts as a repeat only when its tracked actions are exactly one call
    identical to the last one. A batch proposing several different calls, or a
    different call, restarts the run at 1. A batch of only transparent actions leaves
    the chain untouched.
    """
    tracked = _tracked(actions)
    if not tracked:
        return dict(chain or {"signature": None, "count": 0, "name": ""})
    if len(tracked) > 1:
        return {"signature": None, "count": 0, "name": ""}

    action = tracked[0]
    signature = str(action.get("signature") or "")
    name = str(action.get("name") or "action")
    if chain and chain.get("signature") == signature:
        return {"signature": signature, "count": int(chain.get("count", 0)) + 1, "name": name}
    return {"signature": signature, "count": 1, "name": name}


def reminder_for(chain: Dict[str, Any]) -> Optional[str]:
    """The advisory owed at this run length, or ``None`` at a length that says nothing."""
    count = int(chain.get("count", 0))
    if count not in THRESHOLDS:
        return None

    name = chain.get("name") or "the same tool"
    if count == THRESHOLDS[0]:
        return (
            f"You have now called `{name}` {count} times in a row with identical arguments. "
            f"Re-read the result you already have: if it answered the question, act on it; "
            f"if it did not, a fourth identical call will not answer it either — change the "
            f"arguments, try a different tool, or conclude."
        )
    return (
        f"`{name}` has now been called {count} times consecutively with identical arguments: "
        f"{_preview(str(chain.get('signature') or ''))}. "
        f"The result will not differ. Re-read the last one and take a different action — "
        f"different arguments, a different capability, a state-changing step — or, if the "
        f"task's acceptance conditions are already met, call `done_tool` now."
    )


@HOOK.register_module(force=True)
class RepeatToolReminderHook(Hook):
    """Count consecutive identical calls and hand back an escalating reminder."""

    name: str = "repeat_tool_reminder_hook"
    description: str = "Advises, never blocks, when a call repeats verbatim."
    events: list = []
    priority: int = 5

    async def handle(self, ctx: HookContext) -> HookResult:
        inp = ctx.input or {}
        if inp.get("event") != HookEvent.PRE_ACTION:
            return HookResult.allow()

        chain = advance_chain(inp.get("repeat_chain"), inp.get("actions") or [])
        # Always ALLOW: the decision is the model's. The reminder rides as context on
        # the next request, which is the only thing this hook ever does.
        return HookResult(
            additional_context=reminder_for(chain),
            repeat_chain=chain,
        )


__all__ = ["RepeatToolReminderHook", "advance_chain", "reminder_for", "THRESHOLDS", "TRANSPARENT"]
