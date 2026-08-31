"""Execution checkpoints distinguish resumable work from effects needing confirmation.

After a crash, replaying an open mutation can duplicate an external side effect, while
discarding settled receipts loses recoverable progress. These tests derive the safe
decision solely from trace evidence, including compaction and provider state.
"""

from agentevolver.trace.execution_checkpoint import (
    derive_execution_checkpoint,
    reconciliation_event,
)
from agentevolver.trace.request import RequestSnapshot
from agentevolver.trace.types import (
    TraceEvent,
    TraceEventType,
    agent_call_event,
    agent_end_event,
    model_request_event,
    tool_call_event,
    tool_start_event,
)


def _number(events):
    for seq, event in enumerate(events):
        event.seq_no = seq
    return events


def test_open_effect_is_never_retried_automatically():
    events = _number(
        [
            tool_start_event("s", "t", "a", 2, 0, "bash_tool", {"command": "deploy"}, "c1"),
        ]
    )

    checkpoint = derive_execution_checkpoint("s", events)

    assert checkpoint.state == "needs_confirmation"
    assert not checkpoint.may_resume_automatically
    assert checkpoint.unsettled_calls[0].call_id == "c1"
    assert checkpoint.unsettled_calls[0].requires_confirmation


def test_settled_effect_receipt_and_provider_state_are_resumable():
    start = tool_start_event("s", "t", "a", 2, 0, "write_file_tool", {"path": "x"}, "c1")
    result = tool_call_event("s", "t", "a", 2, 0, "write_file_tool", "ok", True, call_id="c1")
    result.metadata["execution"] = {
        "call_id": "c1",
        "workspace_checkpoint": {"path": "/checkpoints/c1.json"},
    }
    step = agent_call_event("s", "t", "a", 2, provider_state={"responses": {"id": "r1"}})

    checkpoint = derive_execution_checkpoint("s", _number([start, result, step]))

    assert checkpoint.state == "resumable"
    assert checkpoint.next_step == 3
    assert checkpoint.provider_state["responses"]["id"] == "r1"
    assert (
        checkpoint.effect_receipts[0].execution["workspace_checkpoint"]["path"].endswith("c1.json")
    )


def test_completed_run_is_not_resumed():
    events = _number([agent_end_event("s", "t", "a", True, "done")])

    checkpoint = derive_execution_checkpoint("s", events)

    assert checkpoint.state == "completed"
    assert not checkpoint.may_resume_automatically
    assert checkpoint.completion_success is True
    assert checkpoint.completion_result == "done"


def test_unclosed_compaction_is_reported_without_blocking_safe_resume():
    event = TraceEvent(
        event_type=TraceEventType.CUSTOM,
        session_id="s",
        metadata={
            "type": "compaction_transaction",
            "transaction_id": "fold-1",
            "phase": "started",
        },
    )

    checkpoint = derive_execution_checkpoint("s", _number([event]))

    assert checkpoint.state == "resumable"
    assert checkpoint.interrupted_compactions == ["fold-1"]


def test_uncertain_background_create_requires_reconciliation_before_resume():
    snapshot = RequestSnapshot.capture(
        requested_model="main",
        routed_model="main",
        model_config=type(
            "Config",
            (),
            {
                "provider": "openai",
                "model_id": "gpt",
                "model_type": "responses",
                "api_base": None,
                "background": None,
            },
        )(),
        client=None,
        messages=[],
        tools=None,
        response_format=None,
        request_input={"background": True},
        call_kwargs={"background": True},
        stream=False,
    )
    request = model_request_event("s", snapshot)

    checkpoint = derive_execution_checkpoint("s", _number([request]))

    assert checkpoint.state == "needs_confirmation"
    assert checkpoint.unsettled_calls[0].action_type == "model_effect"
    assert checkpoint.unsettled_calls[0].action_name == "background.create"


def test_background_result_settles_the_provider_effect():
    snapshot = RequestSnapshot.capture(
        requested_model="main",
        routed_model="main",
        model_config=type(
            "Config",
            (),
            {
                "provider": "openai",
                "model_id": "gpt",
                "model_type": "responses",
                "api_base": None,
                "background": None,
            },
        )(),
        client=None,
        messages=[],
        tools=None,
        response_format=None,
        request_input={"background": True},
        call_kwargs={"background": True},
        stream=False,
    )
    request = model_request_event("s", snapshot)
    result = TraceEvent(
        event_type=TraceEventType.CUSTOM,
        session_id="s",
        success=True,
        metadata={
            "type": "responses_background_effect",
            "phase": "result",
            "operation": "create",
            "response_id": "resp_1",
            "request_snapshot_id": snapshot.snapshot_id,
        },
    )

    checkpoint = derive_execution_checkpoint("s", _number([request, result]))

    assert checkpoint.state == "resumable"
    assert checkpoint.unsettled_calls == []


def test_uncertain_background_cancel_requires_reconciliation_and_result_settles_it():
    snapshot = RequestSnapshot.capture(
        requested_model="main",
        routed_model="main",
        model_config=type(
            "Config",
            (),
            {
                "provider": "openai",
                "model_id": "gpt",
                "model_type": "responses",
                "api_base": None,
                "background": None,
            },
        )(),
        client=None,
        messages=[],
        tools=None,
        response_format=None,
        request_input={"operation": "background.cancel", "background_response_id": "resp_1"},
        call_kwargs={"operation": "background.cancel", "background_response_id": "resp_1"},
        stream=False,
    )
    request = model_request_event("s", snapshot)
    checkpoint = derive_execution_checkpoint("s", _number([request]))
    assert checkpoint.state == "needs_confirmation"
    assert checkpoint.unsettled_calls[0].action_name == "background.cancel"

    result = TraceEvent(
        event_type=TraceEventType.CUSTOM,
        session_id="s",
        success=True,
        metadata={
            "type": "responses_background_effect",
            "phase": "result",
            "operation": "cancel",
            "response_id": "resp_1",
            "request_snapshot_id": snapshot.snapshot_id,
        },
    )
    settled = derive_execution_checkpoint("s", _number([request, result]))
    assert settled.state == "resumable"


def test_unsettled_effect_takes_precedence_over_agent_end():
    request = tool_start_event(
        "s",
        "t",
        "a",
        2,
        0,
        "deploy_tool",
        {"target": "prod"},
        "c1",
    )
    end = agent_end_event("s", "t", "a", True, "done")

    checkpoint = derive_execution_checkpoint("s", _number([request, end]))

    assert checkpoint.state == "needs_confirmation"


def test_human_reconciliation_settles_an_effect_without_replaying_it():
    start = tool_start_event(
        "s", "t", "a", 2, 0, "deploy_tool", {"target": "prod"}, "c1",
    )
    initial = derive_execution_checkpoint("s", _number([start]))

    resolved = reconciliation_event(
        initial, "c1", "applied", "deployment receipt verified",
    )
    settled = derive_execution_checkpoint("s", _number([start, resolved]))

    assert settled.state == "resumable"
    assert settled.unsettled_calls == []
    assert settled.effect_receipts[0].success is True
    assert resolved.metadata["reconciliation"]["authority"] == "human"


def test_not_applied_reconciliation_becomes_a_failed_result_not_an_auto_retry():
    start = tool_start_event(
        "s", "t", "a", 2, 0, "deploy_tool", {"target": "prod"}, "c1",
    )
    initial = derive_execution_checkpoint("s", _number([start]))

    resolved = reconciliation_event(initial, "c1", "not_applied")

    assert resolved.event_type is TraceEventType.TOOL_CALL
    assert resolved.success is False
    assert "not applied" in str(resolved.output)
