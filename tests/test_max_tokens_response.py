"""Buffered consumers must not execute a tool call cut off at max_tokens."""

from __future__ import annotations

import pytest

from agentevolver.model.types import (
    StreamDone,
    ToolCallArgsDelta,
    ToolCallStart,
    build_response_from_stream,
)


@pytest.mark.asyncio
async def test_partial_tool_json_is_preserved_for_diagnosis_but_never_succeeds():
    async def events():
        yield ToolCallStart(index=0, id="c1", name="write_file_tool")
        yield ToolCallArgsDelta(index=0, partial_json='{"path":"/tmp/world.py"')
        yield StreamDone(stop_reason="max_tokens", usage={"output_tokens": 32768})

    response = await build_response_from_stream(events(), tools=[object()])

    assert response.success is False
    assert "max_tokens" in response.message
    partial = response.data["partial_tool_calls"][0]
    assert partial["name"] == "write_file_tool"
    assert partial["input"]["__raw__"] == '{"path":"/tmp/world.py"'
    assert "functions" not in response.data
