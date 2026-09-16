"""What happens to a stream that fails, and where it is allowed to start over.

The streaming path retries and falls back exactly like the buffered one, and none of it
was covered — `model/context.py` lines 1156-1187, the whole `except` arm of the attempt
loop. That block holds a decision that is easy to get wrong in the obvious direction: a
stream that fails *before* emitting anything may be retried, and one that fails *after*
may not, because retrying would repeat the tokens the caller already received.

Upstream reaches these cases by recording every stream chunk and replaying it, which
would mean a capture level on every run to serve tests that can be written with fifteen
lines of stub. The recorded form buys fidelity to a session that really happened; that is
worth having when adapter recovery is the thing under test, and it is not what is under
test here. These drive the loop directly.
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import patch

import pytest

from agentevolver.message import HumanMessage
from agentevolver.model.context import ModelContextManager
from agentevolver.model.types import ModelConfig, ModelContext, StreamDone, TextDelta


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    # Backoff timing is covered in test_model_retry; these tests cover attempt state.
    monkeypatch.setattr("agentevolver.model.context._retry_delay", lambda attempt: 0)


class _Stream:
    """A model client whose stream fails after `emit` events.

    `emit=0` is a stream that never starts — the retryable shape. `emit>0` has already
    handed tokens downstream when it breaks.
    """

    def __init__(self, *, emit: int = 0, fail: bool = True, label: str = "a"):
        self.emit = emit
        self.fail = fail
        self.label = label
        self.attempts = 0

    async def stream(self, **_kwargs):
        self.attempts += 1
        for index in range(self.emit):
            yield TextDelta(text=f"{self.label}{index}")
        if self.fail:
            raise RuntimeError(f"{self.label}: connection reset")
        yield StreamDone(stop_reason="end_turn")  # canonical, not the provider spelling

    def set_api_key(self, _key):
        pass


def _manager(*, retries: int = 3, fallback: str = "") -> ModelContextManager:
    manager = ModelContextManager()
    manager.models["main"] = ModelConfig(
        model_name="main",
        model_type="chat/completions",
        model_id="p/main",
        provider="p",
        max_completion_tokens=500,
        context_window=200_000,
        fallback_model=fallback,
    )
    if fallback:
        manager.models[fallback] = ModelConfig(
            model_name=fallback,
            model_type="chat/completions",
            model_id=f"p/{fallback}",
            provider="p",
            max_completion_tokens=500,
            context_window=200_000,
        )
    return manager


async def _drain(manager: ModelContextManager, *, retries: int = 3) -> List[Any]:
    events = []
    with patch("agentevolver.model.context._record_request_snapshot", side_effect=_noop):
        async for event in manager.stream(
            name="main",
            input={"messages": [HumanMessage(content="go")], "max_retries": retries},
            ctx=ModelContext(id="stream-session"),
        ):
            events.append(event)
    return events


async def _noop(**_kwargs):
    return None


@pytest.mark.asyncio
async def test_a_stream_that_never_started_is_retried():
    """Nothing reached the caller, so starting over is invisible to it."""
    manager = _manager()
    manager.model_clients["main"] = client = _Stream(emit=0)

    with pytest.raises(RuntimeError):
        await _drain(manager, retries=3)

    assert client.attempts == 3, "a stream that emitted nothing was not retried"


@pytest.mark.asyncio
async def test_a_stream_that_already_emitted_is_not_retried():
    """The decision this file exists for.

    Retrying here re-sends the tokens the caller already has, and the caller has no way
    to tell the repeat from new output — it reads as the model saying everything twice.
    """
    manager = _manager()
    manager.model_clients["main"] = client = _Stream(emit=2)

    with pytest.raises(RuntimeError):
        await _drain(manager, retries=3)

    assert client.attempts == 1, "a stream was restarted after it had emitted"


@pytest.mark.asyncio
async def test_what_a_broken_stream_already_yielded_reaches_the_caller():
    """The partial output is not swallowed on the way to the error.

    Discarding it would make a mid-stream failure indistinguishable from one that never
    started, which is exactly the distinction the retry decision rests on.
    """
    manager = _manager()
    manager.model_clients["main"] = _Stream(emit=2)

    events: List[Any] = []
    with pytest.raises(RuntimeError):
        with patch("agentevolver.model.context._record_request_snapshot", side_effect=_noop):
            async for event in manager.stream(
                name="main",
                input={"messages": [HumanMessage(content="go")], "max_retries": 1},
                ctx=ModelContext(id="stream-session"),
            ):
                events.append(event)

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["a0", "a1"]


@pytest.mark.asyncio
async def test_a_model_that_cannot_start_falls_back_to_the_next_one():
    """Exhausting one model's attempts is not exhausting the request."""
    manager = _manager(fallback="spare")
    manager.model_clients["main"] = primary = _Stream(emit=0, label="a")
    manager.model_clients["spare"] = spare = _Stream(emit=1, fail=False, label="b")

    events = await _drain(manager, retries=2)

    assert primary.attempts == 2
    assert spare.attempts == 1
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["b0"]


@pytest.mark.asyncio
async def test_a_fallback_is_not_tried_once_output_has_been_emitted():
    """Same reason as the retry: the caller cannot tell a second model's answer from a
    continuation of the first one's."""
    manager = _manager(fallback="spare")
    manager.model_clients["main"] = _Stream(emit=2, label="a")
    manager.model_clients["spare"] = spare = _Stream(emit=1, fail=False, label="b")

    with pytest.raises(RuntimeError):
        await _drain(manager, retries=2)

    assert spare.attempts == 0, "fell back after the caller had already received output"


@pytest.mark.asyncio
async def test_signature_error_retries_with_shared_budget_and_preserves_unknown_usage(monkeypatch):
    """The website failure: a pre-output APIError must not poison the root ledger."""
    import httpx
    from openai import APIError
    from unittest.mock import AsyncMock
    from agentevolver.runtime.process import RunBudget

    class Flaky(_Stream):
        async def stream(self, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise APIError(
                    "Corrupted thought signature.",
                    request=httpx.Request("POST", "https://example.invalid/chat/completions"),
                    body={"code": 400, "message": "Corrupted thought signature."},
                )
            yield TextDelta(text="recovered")
            yield StreamDone(stop_reason="end_turn", usage={"input_tokens": 10, "output_tokens": 5})

    record = AsyncMock()
    monkeypatch.setattr("agentevolver.model.context._record_retry", record)
    manager = _manager()
    manager.model_clients["main"] = client = Flaky()
    ledger = RunBudget(limit=100_000)
    with ledger.scope():
        events = await _drain(manager, retries=3)
        # The parent's next safe point and a sibling's request also remain usable.
        ledger.check()
        with ledger.request("sibling", 20) as settle:
            settle({"input_tokens": 4, "output_tokens": 1})
    assert client.attempts == 2
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["recovered"]
    unknown = [r for r in ledger.requests.values() if r["status"] == "unknown"]
    assert len(unknown) == 1 and unknown[0]["error_type"] == "APIError"
    assert ledger.reserved == unknown[0]["estimate"] and "usage" not in unknown[0]
    assert ledger.tokens == 20
    record.assert_awaited_once()
    assert "Corrupted thought signature" in record.call_args.kwargs["error"]
    assert record.call_args.kwargs["attempt"] == 1


@pytest.mark.asyncio
async def test_unknown_usage_does_not_remove_retry_limit_or_original_error():
    from agentevolver.runtime.process import RunBudget

    manager = _manager()
    manager.model_clients["main"] = client = _Stream()
    ledger = RunBudget(limit=100_000)
    with ledger.scope(), pytest.raises(RuntimeError, match="connection reset"):
        await _drain(manager, retries=3)
    assert client.attempts == 3
    assert len(ledger.requests) == 3
    assert all(r["status"] == "unknown" and "usage" not in r for r in ledger.requests.values())
    assert ledger.tokens == 0 and ledger.reserved > 0


@pytest.mark.asyncio
async def test_retry_cannot_spend_an_unknown_reservation(monkeypatch):
    from types import SimpleNamespace
    from agentevolver.runtime.process import RunBudget
    from agentevolver.runtime.errors import BudgetExhausted

    manager = _manager()
    manager.model_clients["main"] = client = _Stream()
    monkeypatch.setattr("agentevolver.model.context._prepare_request_messages", lambda **kw: SimpleNamespace(
        messages=kw["messages"], pressure={"estimated_tokens_after": 40, "reserved_output_tokens": 20},
    ))
    ledger = RunBudget(limit=100)
    with ledger.scope(), pytest.raises(BudgetExhausted, match="Insufficient"):
        await _drain(manager, retries=3)
    assert client.attempts == 1 and ledger.reserved == 60


@pytest.mark.asyncio
async def test_partial_stream_keeps_reservation_but_is_not_replayed():
    from agentevolver.runtime.process import RunBudget

    manager = _manager(fallback="spare")
    manager.model_clients["main"] = client = _Stream(emit=1)
    manager.model_clients["spare"] = spare = _Stream(fail=False)
    ledger = RunBudget(limit=100_000)
    with ledger.scope(), pytest.raises(RuntimeError, match="connection reset"):
        await _drain(manager, retries=3)
    assert client.attempts == 1 and spare.attempts == 0
    assert ledger.tokens == 0 and ledger.reserved > 0
    ledger.check()
