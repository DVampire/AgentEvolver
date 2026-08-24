"""Repetition is advised against, not vetoed.

The predecessor blocked a repeated call, and to decide what was blockable it sorted
tools by substring: a name containing `poll` or `watch` was exempt, everything else
was fair game. A name is not a behaviour, so the classification mislabelled honest
tools, and the block acted on the mislabel irreversibly.

Verbatim repetition is the one thing that can be established without interpreting
anything, so it is all this hook claims — and the strongest thing it does about the
claim is speak. Blocking is left to the idle-turn backstop, which judges the
workspace rather than the tool's name.
"""

import json

import pytest

from agentevolver.hook.default.repeat_tool import (
    THRESHOLDS,
    TRANSPARENT,
    advance_chain,
    reminder_for,
)


def _action(name, **args):
    signature = json.dumps({"type": "tool", "name": name, "args": args},
                           ensure_ascii=False, sort_keys=True, default=str)
    return {"name": name, "type": "tool", "signature": signature}


def _run(batches, chain=None):
    for batch in batches:
        chain = advance_chain(chain, batch)
    return chain


# --------------------------------------------------------------------------- #
# The chain
# --------------------------------------------------------------------------- #
def test_identical_calls_accumulate():
    chain = _run([[_action("grep_search_tool", pattern="x")]] * 3)
    assert chain["count"] == 3


def test_a_different_call_restarts_the_run():
    chain = _run([
        [_action("grep_search_tool", pattern="x")],
        [_action("grep_search_tool", pattern="x")],
        [_action("grep_search_tool", pattern="y")],
    ])
    assert chain["count"] == 1


def test_argument_order_is_not_a_difference():
    """Canonicalisation is a deep key sort, so the model's key order cannot hide a repeat."""
    chain = _run([[_action("t", a=1, b=2)], [_action("t", b=2, a=1)]])
    assert chain["count"] == 2


def test_bookkeeping_between_repeats_does_not_launder_the_loop():
    """This is what makes the exclusion list worth having.

    A loop with `inspect_tool` interleaved is still a loop; if the bookkeeping call reset
    the chain, an agent could repeat forever and never be told.

    Was written against `todo_tool`, which is gone — the running plan is `plan.md` now,
    kept with the ordinary file tools. Those are deliberately *not* transparent: editing
    a file is work, and a run that alternates edit-and-repeat is not looping.
    """
    chain = _run([
        [_action("grep_search_tool", pattern="x")],
        [_action("inspect_tool", capability_type="tool", target="bash_tool")],
        [_action("grep_search_tool", pattern="x")],
    ])
    assert chain["count"] == 2
    assert "inspect_tool" in TRANSPARENT
    assert "write_file_tool" not in TRANSPARENT and "edit_file_tool" not in TRANSPARENT


def test_a_multi_call_batch_repeats_like_any_other():
    """The unit is the batch. Keying on a single call made this shape invisible.

    A real run proposed the same three calls together on seven consecutive turns and
    scored zero every turn, because a multi-call batch reset the chain by definition —
    while the identical `read_file_tool` inside it was issued thirteen times.
    """
    batch = [_action("read_file_tool", path="a.py"),
             _action("read_file_tool", path="b.py"),
             _action("bash_tool", command="pytest")]
    chain = _run([batch] * 3)

    assert chain["count"] == 3
    assert reminder_for(chain), "the shape that actually loops must be able to speak"


def test_a_different_batch_restarts_the_run():
    chain = _run([
        [_action("t", a=1), _action("u", b=2)],
        [_action("t", a=1), _action("u", b=2)],
        [_action("t", a=1)],                      # one call dropped: different work
    ])
    assert chain["count"] == 1


def test_call_order_within_a_batch_is_not_a_difference():
    """Parallel calls are unordered work, for the same reason argument order is."""
    a, b = _action("t", x=1), _action("u", y=2)
    assert _run([[a, b], [b, a]])["count"] == 2


# --------------------------------------------------------------------------- #
# What it says
# --------------------------------------------------------------------------- #
def test_it_stays_quiet_below_the_first_threshold():
    for count in range(1, THRESHOLDS[0]):
        assert reminder_for({"count": count, "names": ["t"], "signature": []}) is None


def test_the_first_reminder_is_a_short_nudge():
    text = reminder_for({"count": THRESHOLDS[0], "names": ["grep_search_tool"], "signature": []})
    assert text and "grep_search_tool" in text
    assert str(THRESHOLDS[0]) in text
    assert "this exact call" in text, "a one-call batch should not read as a set"


def test_a_multi_call_reminder_names_every_call_in_the_batch():
    text = reminder_for({"count": THRESHOLDS[0],
                         "names": ["read_file_tool", "bash_tool"], "signature": []})
    assert text and "read_file_tool" in text and "bash_tool" in text
    assert "set of calls" in text


def test_later_reminders_quote_the_arguments():
    signature = [json.dumps({"type": "tool", "name": "t", "args": {"pattern": "needle"}})]
    text = reminder_for({"count": THRESHOLDS[1], "names": ["t"], "signature": signature})
    assert text and "needle" in text
    assert "done_tool" in text          # names the exit, not just the problem


def test_a_huge_payload_is_not_quoted_back_whole():
    """The chain key compares the full signature; only the reminder is bounded.

    Without this, a looping `write_file_tool` carrying a large payload would copy that
    payload into every reminder, i.e. into the next request.
    """
    signature = [json.dumps({"type": "tool", "name": "w", "args": {"content": "Z" * 50_000}})]
    text = reminder_for({"count": THRESHOLDS[1], "names": ["w"], "signature": signature})
    assert text is not None
    assert len(text) < 2_000
    assert "more characters" in text     # says what it dropped


def test_only_the_declared_thresholds_speak():
    """Every repeat speaking would be nagging; the run lengths that speak are chosen."""
    said = [c for c in range(1, 12)
            if reminder_for({"count": c, "names": ["t"], "signature": []})]
    assert said == [t for t in THRESHOLDS if t < 12]


# --------------------------------------------------------------------------- #
# The hook contract
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_hook_never_blocks():
    """The whole point. A legitimately repeated call must be delayed by nothing."""
    from agentevolver.hook.default.repeat_tool import RepeatToolReminderHook
    from agentevolver.hook.types import HookContext, HookDecision, HookEvent

    hook = RepeatToolReminderHook()
    chain = None
    for _ in range(12):
        result = await hook.handle(HookContext(
            id="h", name="repeat_tool_reminder_hook",
            input={"event": HookEvent.PRE_ACTION,
                   "actions": [_action("t", a=1)],
                   "repeat_chain": chain},
        ))
        assert result.decision == HookDecision.ALLOW
        chain = result.repeat_chain

    assert chain["count"] == 12          # it kept counting all the way


@pytest.mark.asyncio
async def test_the_hook_holds_no_state_between_runs():
    """Two concurrent runs must not trip one another's reminder."""
    from agentevolver.hook.default.repeat_tool import RepeatToolReminderHook
    from agentevolver.hook.types import HookContext, HookEvent

    hook = RepeatToolReminderHook()

    async def once(chain):
        result = await hook.handle(HookContext(
            id="h", name="repeat_tool_reminder_hook",
            input={"event": HookEvent.PRE_ACTION,
                   "actions": [_action("t", a=1)],
                   "repeat_chain": chain},
        ))
        return result.repeat_chain

    a = await once(await once(await once(None)))    # run A repeats three times
    b = await once(None)                            # run B has repeated once
    assert a["count"] == 3
    assert b["count"] == 1
