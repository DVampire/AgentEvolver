"""Optional step policies and progress advice; base resource limits live in the loop.

Each guard is ``async (agent, step) -> str``. What it returns rides in this step's live
layer, past the cache breakpoint, where changing it costs nothing.

Shipped: the budget window, the two ways a run stalls, and the capability-change
announcement. The previous design carried more than ten, each appending to one shared
list, each unable to see what the others had already said. Adding one here is a
deliberate act with a name and a place, not another branch inside the loop.

Advice to the model lives here and only here. It used to be split: `NoProgress` was
middleware while verbatim repetition was `repeat_tool_reminder_hook`, a hook that
subscribed to nothing and was called by nobody — registered, documented as active, and
dead. Two mechanisms for one job is how one of them goes quiet without anyone noticing.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Sequence

from agentevolver.agent.loop.events import events
from agentevolver.logger import logger
from agentevolver.message.types import AssistantMessage
from agentevolver.runtime.errors import BudgetExhausted

#: Steps reserved at the end of a run for landing rather than starting.
DEFAULT_RESERVE_STEPS = 3

#: Consecutive read-only turns before the agent is told it is circling.
DEFAULT_IDLE_TURNS = 6


class LandingWindow:
    """Tell the agent when its remaining budget is only enough to finish.

    A run cut off mid-action ships whatever it happened to have persisted. The last few
    steps are worth more spent making the work durable than spent starting something
    that cannot land, and the agent cannot see this for itself — it knows the step
    number, not what the number means.
    """

    def __init__(self, reserve: int = DEFAULT_RESERVE_STEPS) -> None:
        self.reserve = max(1, int(reserve))

    async def __call__(self, agent: Any, step: int) -> str:
        remaining = agent.max_step - step
        if remaining > self.reserve:
            return ""
        return (
            f"<budget>\nOnly {remaining} step(s) remain before a hard stop that "
            "discards any unfinished action. Do not start anything new. Persist what "
            "you already have — save, commit, or write out your files — then give your "
            "result.\n</budget>"
        )


class NoProgress:
    """Notice a run that keeps looking and never changes anything.

    Judged on the actions themselves rather than on repetition: many *different*
    measurements with nothing changed between them is the shape that actually consumes a
    budget, and no repeat detector can see it because every call differs.

    Advice, not a veto. The agent is told once it crosses the threshold, and again every
    turn it stays there; whether to act on it is the model's decision.
    """

    def __init__(self, after: int = DEFAULT_IDLE_TURNS) -> None:
        self.after = max(2, int(after))

    async def __call__(self, agent: Any, step: int) -> str:
        idle = self._idle_turns(agent)
        if idle < self.after:
            return ""
        return (
            f"<no-progress>\nThe last {idle} turns only inspected — nothing was "
            "written or changed. You have measured enough to act. Make the change you "
            "believe is right; a wrong edit is visible in one turn and reversible, "
            "while another measurement tells you what the last one did. If you cannot "
            "name a change to make, this is not the thing to work on: say so and move "
            "on.\n</no-progress>"
        )

    def _idle_turns(self, agent: Any) -> int:
        """Consecutive most-recent turns whose every action was read-only."""
        routing = getattr(agent, "_routing", {}) or {}
        router = getattr(agent, "router", None)
        if router is None:
            return 0
        turns: List[AssistantMessage] = [
            message for message in getattr(agent.conversation, "items", [])
            if isinstance(message, AssistantMessage)
        ]
        idle = 0
        for message in reversed(turns):
            if not message.tool_calls:
                break
            names = [call.function.name for call in message.tool_calls]
            if all(self._read_only(router, routing, name) for name in names):
                idle += 1
                continue
            break
        return idle

    @staticmethod
    def _read_only(router: Any, routing: dict, name: str) -> bool:
        from agentevolver.agent.loop.decision import ActionCall

        try:
            return router.read_only(ActionCall(id="", name=name), routing) is True
        except Exception:  # noqa: BLE001 - unknown counts as effectful
            return False


#: Run lengths of an identical batch that are worth saying something about. The first is
#: a short nudge; every later one names the calls, the run length, and the arguments.
#: Ascending, and each at least 2 — a "run" of one is just a call.
REPEAT_THRESHOLDS: Sequence[int] = (3, 5, 8)

#: Calls that neither advance nor break a run. Bookkeeping the model interleaves into a
#: loop must not launder it: with `inspect_tool` transparent,
#: `grep X → inspect_tool → grep X` is still two consecutive `grep X`.
TRANSPARENT = frozenset({"journal_tool", "inspect_tool", "reply_tool"})

#: Cap on arguments quoted back in a reminder. The run is always counted on the FULL
#: signature; this bounds only what rides into the next request, so a looping `write`
#: carrying a large payload cannot grow the prompt without limit.
ARGS_PREVIEW_CHARS = 500


class RepeatedActions:
    """Notice a batch issued verbatim several turns running, and say so.

    Complementary to :class:`NoProgress`, not a weaker version of it. `NoProgress`
    catches many *different* read-only measurements, which no repeat detector can see
    because every call differs; this catches the identical call issued again, which
    `NoProgress` cannot see because a repeated *write* is not read-only. Both are
    advice, and neither claims to have measured progress — the one fact claimed here is
    that the model asked for the same thing twice.

    The unit is the batch, not the call. Keying on a single call was a faithful port of a
    harness that dispatches one at a time, and it was blind here: an agent proposing the
    same three calls together on seven consecutive turns scored zero every turn, because
    a multi-call batch reset the count by definition.

    Stateless, like every guard: the run is recomputed from the conversation each step,
    so concurrent sessions cannot trip one another and nothing has to be carried between
    turns.
    """

    def __init__(self, thresholds: Sequence[int] = REPEAT_THRESHOLDS) -> None:
        self.thresholds = tuple(sorted({max(2, int(n)) for n in thresholds}))

    async def __call__(self, agent: Any, step: int) -> str:
        run, names, key = self._trailing_run(agent)
        if run not in self.thresholds:
            return ""
        return self._reminder(run, names, key)

    def _trailing_run(self, agent: Any) -> tuple:
        """How many of the most recent turns proposed the identical batch."""
        turns = [
            message for message in getattr(agent.conversation, "items", [])
            if isinstance(message, AssistantMessage) and message.tool_calls
        ]
        run, names, key = 0, [], []
        for message in reversed(turns):
            batch = self._batch(message)
            if not batch:
                # Only transparent calls: neither advances the run nor breaks it.
                continue
            if key and batch != key:
                break
            key = batch
            names = self._names(message)
            run += 1
        return run, names, key

    @staticmethod
    def _tracked(message: AssistantMessage) -> List[Any]:
        return [
            call for call in message.tool_calls
            if str(getattr(call.function, "name", "") or "") not in TRANSPARENT
        ]

    @classmethod
    def _names(cls, message: AssistantMessage) -> List[str]:
        return [str(call.function.name or "action") for call in cls._tracked(message)]

    @classmethod
    def _batch(cls, message: AssistantMessage) -> List[str]:
        """One turn's tracked calls as canonical signatures, in a stable order.

        Sorted, so the same parallel calls issued in a different order are the same
        batch — for the same reason each signature sorts its argument keys: neither
        ordering changes the work being asked for.
        """
        signatures = []
        for call in cls._tracked(message):
            try:
                args = json.loads(call.function.arguments or "{}")
            except (TypeError, ValueError):
                args = {"__raw": str(call.function.arguments or "")}
            signatures.append(json.dumps(
                {"name": call.function.name, "args": args},
                ensure_ascii=False, sort_keys=True, default=str,
            ))
        return sorted(signatures)

    def _reminder(self, run: int, names: List[str], key: List[str]) -> str:
        what = ", ".join(f"`{name}`" for name in names) or "the same tool"
        subject = "this exact set of calls" if len(names) > 1 else "this exact call"
        if run == self.thresholds[0]:
            return (
                f"<repeated>\nYou have now issued {subject} {run} times in a row, "
                f"with identical arguments each time: {what}. Re-read the result you "
                f"already have: if it answered the question, act on it; if it did not, "
                f"issuing it again will not answer it either — change the arguments, try "
                f"a different capability, or conclude.\n</repeated>"
            )
        return (
            f"<repeated>\n{subject.capitalize()} has now been issued {run} times "
            f"consecutively with identical arguments — {what}: {self._preview(key)}. "
            f"The result will not differ. Re-read the last one and take a different "
            f"action — different arguments, a different capability, a state-changing "
            f"step — or, if the task's acceptance conditions are already met, call "
            f"`done_tool` now.\n</repeated>"
        )

    @staticmethod
    def _preview(key: Sequence[str]) -> str:
        """The batch's arguments, bounded, with what was dropped stated."""
        parts = []
        for signature in key:
            try:
                parts.append(json.dumps(
                    json.loads(signature).get("args", {}),
                    ensure_ascii=False, sort_keys=True, default=str,
                ))
            except (TypeError, ValueError, AttributeError):
                parts.append(str(signature))
        text = " | ".join(parts)
        if len(text) <= ARGS_PREVIEW_CHARS:
            return text
        dropped = len(text) - ARGS_PREVIEW_CHARS
        return f"{text[:ARGS_PREVIEW_CHARS]} […{dropped:,} more characters]"


class Constraints:
    """Additional host-configured constraint policies.

    A policy rather than an observer — it can end a run — so it is asked by name and its
    verdict is binding. It is stateful: every call counts a step, so it runs exactly once
    per step, which is what being a middleware guarantees.

    Returns the budget snapshot as text for the live layer, and raises
    :class:`BudgetExhausted` when one is spent. The agent turns that into a stop, so the
    guard decides *whether* the budget is gone and the loop decides what a stop means.
    """

    def __init__(self, constraints: Sequence[Any] = ()) -> None:
        self.constraints = list(constraints)

    async def __call__(self, agent: Any, step: int) -> str:
        if not self.constraints:
            return ""
        from agentevolver.hook.types import HookDecision, HookEvent

        ctx = getattr(agent, "ctx", None)
        verdict = await events.gate(
            "constraint_hook",
            {
                "event": HookEvent.PRE_STEP,
                "agent_name": agent.name,
                "task_id": getattr(getattr(agent, "proc", None), "pid", "") or "",
                "constraint_names": [c.name for c in self.constraints],
                "check_input": {
                    "token": agent.spend_step_tokens(),
                    "max_step": agent.max_step,
                    "max_token": getattr(agent, "max_token", None),
                    "max_second": getattr(agent, "timeout", None),
                },
            },
            ctx=ctx,
        )
        if getattr(verdict, "decision", None) is HookDecision.BLOCK:
            reason = str(getattr(verdict, "reason", "") or "a resource budget is spent")
            for constraint in self.constraints:
                # Released by the key the constraint actually counts under.
                try:
                    constraint._cleanup(getattr(ctx, "id", None))
                except Exception as error:  # noqa: BLE001
                    logger.debug(f"| ⚙️ constraint cleanup: {error}")
            raise BudgetExhausted(reason)
        return render_status(getattr(verdict, "constraint_status", None) or [])




def render_status(status: Sequence[Any]) -> str:
    """The budget snapshot as the model reads it."""
    if not status:
        return ""
    try:
        from agentevolver.constraint import render_status_text

        text = render_status_text(list(status))
    except Exception:  # noqa: BLE001 - a snapshot is never worth failing a step over
        text = "\n".join(str(item) for item in status)
    return f"<budget>\n{text}\n</budget>" if text else ""


class CapabilityChanges:
    """Say what appeared or went away, when the registry changes mid-run.

    Self-evolution registers a component while a run is in progress, and the tool list
    changes with it. The model would eventually notice — the new function is simply
    there next turn — but "eventually" means it keeps working around a gap it no longer
    has, and a withdrawn capability keeps getting called until a failure explains it.

    Announced in the live layer, past the cache breakpoint, so saying it costs nothing.
    The previous implementation froze a rendered catalog in the prompt to protect the
    prefix behind it; the catalog is not in the prompt any more, so what is left is the
    announcement, which is the half the model actually reads.
    """

    def __init__(self) -> None:
        self._revision: Optional[int] = None
        self._known: set[str] = set()

    async def __call__(self, agent: Any, step: int) -> str:
        from agentevolver.extension import extension_manager

        revision = int(extension_manager.capability_revision)
        names = set((getattr(agent, "_routing", None) or {}).keys())
        if self._revision is None:
            self._revision, self._known = revision, names
            return ""
        if revision == self._revision:
            # The roster is rebuilt only when the revision moves, so an unchanged
            # revision means an unchanged roster and there is nothing to say.
            return ""

        added = sorted(names - self._known)
        removed = sorted(self._known - names)
        self._revision, self._known = revision, names
        if not added and not removed:
            return ""

        lines = [f"  now available: {name}" for name in added]
        lines += [f"  no longer available, do not call: {name}" for name in removed]
        return (
            "<capability-changes>\nThe capabilities you can call have changed since "
            "this conversation started:\n" + "\n".join(lines) + "\n</capability-changes>"
        )


__all__ = [
    "ARGS_PREVIEW_CHARS",
    "BudgetExhausted",
    "CapabilityChanges",
    "Constraints",
    "DEFAULT_IDLE_TURNS",
    "DEFAULT_RESERVE_STEPS",
    "LandingWindow",
    "NoProgress",
    "REPEAT_THRESHOLDS",
    "RepeatedActions",
    "TRANSPARENT",
    "render_status",
]
