"""Repetition is advised against, not vetoed, and the advice actually reaches the model.

Two histories are pinned here.

The first is why it only advises. The predecessor blocked a repeated call, and to decide
what was blockable it sorted tools by substring: a name containing `poll` or `watch` was
exempt, everything else was fair game. A name is not a behaviour, so the classification
mislabelled honest tools, and the block acted on the mislabel irreversibly. Verbatim
repetition is the one thing that can be established without interpreting anything, so it
is all this claims, and the strongest thing it does about the claim is speak.

The second is why it is middleware. It used to be `repeat_tool_reminder_hook`, which
subscribed to no event (`events: list = []`), was called by name from nowhere, and was
not among the loop's two named observers — registered, documented as active, and never
once executed. Advice to the model has exactly one channel now, `agent.middleware`, and
this test drives it through that channel rather than calling the pure functions.
"""

import json

import pytest

from agentevolver.agent.loop.guards import (
    ARGS_PREVIEW_CHARS,
    REPEAT_THRESHOLDS,
    TRANSPARENT,
    RepeatedActions,
)
from agentevolver.message.types import AssistantMessage, Function, ToolCall

_ids = iter(range(1, 10**6))


def _call(name, **args):
    return ToolCall(
        id=f"c{next(_ids)}",
        type="function",
        function=Function(name=name, arguments=json.dumps(args, sort_keys=True)),
    )


class _Conversation:
    def __init__(self, items):
        self.items = items


def _agent(*batches):
    """An agent whose history is one assistant turn per batch, oldest first."""
    items = [AssistantMessage(content="", tool_calls=list(batch)) for batch in batches]
    return type("Probe", (), {"conversation": _Conversation(items)})()


async def _advice(*batches, guard=None):
    return await (guard or RepeatedActions())(_agent(*batches), step=len(batches))


# ---------------------------------------------------------------------------
# What counts as the same batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_calls_accumulate_until_a_threshold_speaks():
    one = _call("grep_search", query="needle")
    assert await _advice([one], [one]) == ""
    assert "3 times in a row" in await _advice([one], [one], [one])


@pytest.mark.asyncio
async def test_a_different_call_restarts_the_run():
    same = _call("grep_search", query="needle")
    other = _call("grep_search", query="haystack")
    # Three in a row would speak; the newest batch differs, so the run is 1.
    assert await _advice([same], [same], [other]) == ""


@pytest.mark.asyncio
async def test_argument_order_is_not_a_difference():
    """The signature sorts its keys: the same work asked for the same way."""
    first = ToolCall(id="a", type="function", function=Function(
        name="read", arguments='{"path": "x", "limit": 5}'))
    second = ToolCall(id="b", type="function", function=Function(
        name="read", arguments='{"limit": 5, "path": "x"}'))
    assert "3 times in a row" in await _advice([first], [second], [first])


@pytest.mark.asyncio
async def test_call_order_within_a_batch_is_not_a_difference():
    left, right = _call("read", path="a"), _call("read", path="b")
    assert "3 times" in await _advice([left, right], [right, left], [left, right])


@pytest.mark.asyncio
async def test_a_multi_call_batch_repeats_like_any_other():
    """The unit is the batch.

    Keying on a single call was a faithful port of a harness that dispatches one at a
    time, and it was blind here: an agent proposing the same three calls together on
    seven consecutive turns scored zero every turn, because a multi-call batch reset the
    count by definition.
    """
    batch = [_call("read", path="a"), _call("grep_search", query="q")]
    advice = await _advice(batch, batch, batch)
    assert "this exact set of calls" in advice
    assert "`read`" in advice and "`grep_search`" in advice


@pytest.mark.asyncio
async def test_a_different_batch_restarts_the_run():
    batch = [_call("read", path="a"), _call("read", path="b")]
    shorter = [_call("read", path="a")]
    assert await _advice(batch, batch, shorter) == ""


# ---------------------------------------------------------------------------
# What the loop cannot launder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bookkeeping_between_repeats_does_not_launder_the_loop():
    """`grep X → inspect_tool → grep X` is still two consecutive `grep X`."""
    assert TRANSPARENT, "the transparent set is the whole mechanism here"
    real = _call("grep_search", query="needle")
    noise = _call(sorted(TRANSPARENT)[0])
    assert "3 times in a row" in await _advice([real], [noise], [real], [real])


@pytest.mark.asyncio
async def test_a_turn_of_only_transparent_calls_never_speaks_on_its_own():
    noise = _call(sorted(TRANSPARENT)[0])
    assert await _advice([noise], [noise], [noise], [noise]) == ""


# ---------------------------------------------------------------------------
# What it says, and when it stays quiet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_declared_thresholds_speak():
    one = _call("read", path="x")
    spoke = {
        run: bool(await _advice(*[[one]] * run))
        for run in range(1, max(REPEAT_THRESHOLDS) + 2)
    }
    assert {run for run, said in spoke.items() if said} == set(REPEAT_THRESHOLDS)


@pytest.mark.asyncio
async def test_a_later_reminder_quotes_the_arguments_and_names_done_tool():
    one = _call("read", path="/work/app.py")
    advice = await _advice(*[[one]] * REPEAT_THRESHOLDS[1])
    assert "/work/app.py" in advice
    assert "done_tool" in advice


@pytest.mark.asyncio
async def test_a_huge_payload_is_not_quoted_back_whole():
    """The run is counted on the full signature; only the quote is bounded.

    A looping `write` carrying a large payload would otherwise grow the next request by
    its whole body, every turn it kept looping.
    """
    fat = _call("write", path="x", content="y" * 40_000)
    advice = await _advice(*[[fat]] * REPEAT_THRESHOLDS[1])
    assert "more characters]" in advice
    assert len(advice) < ARGS_PREVIEW_CHARS + 1_000


@pytest.mark.asyncio
async def test_it_holds_no_state_between_runs():
    """Stateless, so two concurrent sessions cannot trip one another."""
    guard = RepeatedActions()
    one = _call("read", path="x")
    assert "3 times in a row" in await _advice([one], [one], [one], guard=guard)
    assert await _advice([one], guard=guard) == ""


@pytest.mark.asyncio
async def test_it_never_vetoes_anything():
    """Its whole output is a string for the live layer. There is no other channel."""
    one = _call("read", path="x")
    advice = await _advice(*[[one]] * REPEAT_THRESHOLDS[0])
    assert isinstance(advice, str) and advice
