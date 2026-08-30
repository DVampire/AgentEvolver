"""The model-request viewer is a safe side view of the canonical trace snapshot."""

from datetime import datetime, timezone
import re
from pathlib import Path

import pytest

from agentevolver.trace.types import TraceEvent, TraceEventType
from agentevolver.visual.request_viewer import (
    flush_request_html,
    render_request_html,
    request_html_path,
    schedule_request_html,
    write_request_html,
)


def _event() -> TraceEvent:
    return TraceEvent(
        event_type=TraceEventType.MODEL_REQUEST,
        session_id="session-1",
        task_id="task-1",
        agent_name="code/agent",
        step_number=7,
        seq_no=42,
        timestamp=datetime(2026, 8, 31, tzinfo=timezone.utc),
        metadata={
            "attempt": 2,
            "route_index": 1,
            "request_snapshot_id": "sha256:1234567890abcdef",
        },
        input={
            "schema_version": 2,
            "snapshot_id": "sha256:1234567890abcdef",
            "requested_model": "primary",
            "routed_model": "fallback",
            "provider": "anthropic",
            "provider_model": "claude-opus-5",
            "model_type": "messages",
            "parameters": {"temperature": 0, "stream": True},
            "messages": [
                {"role": "system", "cache": True, "content": "<script>alert('x')</script>"},
                {"role": "user", "cache": True, "content": "<task>repair it</task>"},
                {
                    "role": "assistant",
                    "cache": False,
                    "content": "I will inspect it.",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command":"pwd"}'},
                    }],
                    "provider_state": {"anthropic": {"signature": "opaque"}},
                },
                {
                    "role": "tool",
                    "cache": False,
                    "name": "bash",
                    "tool_call_id": "call-1",
                    "is_error": False,
                    "content": "/workspace",
                },
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "run a command",
                    "parameters": {"type": "object"},
                },
            }],
            "response_format": None,
            "pressure": {
                "tier": "normal",
                "estimated_tokens_after": 40_000,
                "input_capacity_tokens": 200_000,
                "compaction_policy": {
                    "compact_after_steps": 30,
                    "compact_at_tokens": 120_000,
                },
            },
        },
    )


def test_request_page_preserves_roles_calls_cache_and_route_metadata(tmp_path):
    event = _event()
    page = render_request_html(event, str(tmp_path / "request.html"))

    assert 'class="message-card role-system layer-fixed"' in page
    assert 'class="message-card role-assistant layer-recent"' in page
    assert 'class="message-card role-tool layer-recent"' in page
    assert "cache boundary" in page
    assert "Provider state" in page
    assert "call-1" in page
    assert "claude-opus-5" in page
    assert "attempt-02" not in page  # filename metadata is not confused with content
    assert "Canonical JSON" in page
    assert "Context map" in page
    assert 'id="layer-filter"' in page
    assert 'id="cache-filter"' in page
    assert "Endpoint fingerprint" in page
    assert 'class="sequence-node role-system cache-boundary"' in page
    assert 'id="message-4"' in page
    assert "Request diagnostics" in page
    assert "Fixed prefix" in page
    assert "Body after prefix" in page
    assert "Provider state" in page
    assert "Tool arguments" in page
    assert "Compaction" in page
    assert "Context capacity" in page
    assert "pending" in page


def test_request_page_normalizes_anthropic_cache_usage(tmp_path):
    page = render_request_html(
        _event(),
        str(tmp_path / "request.html"),
        usage={
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_tokens": 900,
            "cache_write_tokens": 0,
        },
    )

    assert "90.0%" in page
    assert "900 read tokens" in page


def test_llm_hub_anthropic_protocol_uses_anthropic_cache_accounting(tmp_path):
    event = _event()
    event.input["provider"] = "llm_hub"
    event.input["model_type"] = "anthropic/messages"

    page = render_request_html(
        event,
        str(tmp_path / "request.html"),
        usage={"input_tokens": 100, "output_tokens": 10, "cache_read_tokens": 900},
    )

    assert "90.0%" in page


def test_request_content_is_escaped_and_never_executed_as_page_markup(tmp_path):
    page = render_request_html(_event(), str(tmp_path / "request.html"))

    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in page
    assert "<script>alert('x')</script>" not in page


def test_request_filename_is_unique_for_retry_route_and_sanitizes_agent_name(tmp_path):
    path = request_html_path(str(tmp_path), _event())

    assert path.endswith(
        "model_requests/code_agent/000042-step-0007-attempt-02-route-01-1234567890.html"
    )


def test_request_page_is_written_atomically_and_links_shared_assets(tmp_path):
    path = write_request_html(_event(), str(tmp_path))
    page = open(path, encoding="utf-8").read()

    assert path.endswith(".html")
    assert '<link rel="stylesheet" href="' in page
    assert '<script defer src="' in page
    assert "<style>" not in page
    assert "<script defer>" not in page
    css_href = re.search(r'<link rel="stylesheet" href="([^"]+)"', page).group(1)
    js_src = re.search(r'<script defer src="([^"]+)"', page).group(1)
    assert (Path(path).parent / css_href).resolve().name == "request.css"
    assert (Path(path).parent / js_src).resolve().name == "request.js"
    assert (Path(path).parent / css_href).is_file()
    assert (Path(path).parent / js_src).is_file()
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_background_request_page_can_be_flushed_before_shutdown(tmp_path):
    event = _event()
    schedule_request_html(event, str(tmp_path))
    schedule_request_html(
        event,
        str(tmp_path),
        usage={"input_tokens": 100, "cache_read_tokens": 900},
    )

    assert await flush_request_html()
    assert len(list(tmp_path.rglob("*.html"))) == 1
    assert "90.0%" in next(tmp_path.rglob("*.html")).read_text(encoding="utf-8")


def test_visual_pages_share_one_palette_and_type_system():
    css_dir = Path(__file__).resolve().parents[1] / "agentevolver" / "visual" / "css"
    pages = ["memory.css", "plan.css", "prompt.css", "request.css", "task.css"]
    tokens = (
        "ground", "surface", "surface-2", "surface-3", "text", "text-mid",
        "text-faint", "green", "amber", "red", "blue", "border", "border-hi", "mono",
    )

    values = {}
    for name in pages:
        source = (css_dir / name).read_text(encoding="utf-8")
        values[name] = {
            token: re.search(rf"--{re.escape(token)}:\s*([^;]+);", source).group(1).strip()
            for token in tokens
        }

    expected = values["prompt.css"]
    assert all(palette == expected for palette in values.values()), values


@pytest.mark.asyncio
async def test_post_step_refreshes_the_matching_request_with_provider_usage(monkeypatch):
    from agentevolver.hook.default.trace import TraceHook
    from agentevolver.hook.types import HookContext, HookEvent
    from agentevolver.memory import memory_manager
    from agentevolver.trace import trace_manager
    import agentevolver.visual.request_viewer as viewer

    request = _event()
    retained = [request]
    scheduled = []

    async def emit(event):
        event.seq_no = 43
        retained.append(event)
        return True

    async def consume(*args, **kwargs):
        return None

    monkeypatch.setattr(trace_manager, "emit", emit)
    monkeypatch.setattr(trace_manager, "events", lambda session_id: list(retained))
    monkeypatch.setattr(trace_manager, "_log_root", "/tmp/session/log/trace")
    monkeypatch.setattr(memory_manager, "consume_trace_event", consume)
    monkeypatch.setattr(
        viewer,
        "schedule_request_html",
        lambda event, root, **kwargs: scheduled.append((event, root, kwargs)),
    )

    usage = {"input_tokens": 100, "output_tokens": 5, "cache_read_tokens": 900}
    await TraceHook().handle(HookContext(
        id="session-1",
        name="trace_hook",
        input={
            "event": HookEvent.POST_STEP,
            "agent_name": "code/agent",
            "task_id": "task-1",
            "step_number": 7,
            "step_usage": usage,
            "use_memory": False,
        },
    ))

    assert scheduled and scheduled[0][0] is request
    assert scheduled[0][2]["usage"] == usage
