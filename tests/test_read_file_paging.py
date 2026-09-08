"""File paging and explicit recovery through the real tool/Agent response boundary."""
import asyncio
from types import SimpleNamespace

import pytest

from agentevolver.agent.loop.decision import ActionCall
from agentevolver.agent.loop.router import CapabilityRouter
from agentevolver.tool.default.workspace.read_file import ReadFileTool
from agentevolver.tool.types import OUTPUT_LIMIT
from test_tool_robustness import _manager_for


def test_default_pages_recover_every_line_without_gaps(tmp_path):
    path = tmp_path / "large.txt"
    path.write_text("".join(f"line-{i}\n" for i in range(1, 452)))
    tool = ReadFileTool()
    offset, lines = 1, []
    while offset is not None:
        result = asyncio.run(tool(path=str(path), offset=offset))
        assert result.success
        lines.extend(line for line in result.message.splitlines() if "\t" in line)
        offset = result.data["next_offset"]
    assert lines == [f"{i}\tline-{i}" for i in range(1, 452)]


def test_explicit_full_read_survives_manager_and_model_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    path = tmp_path / "long-line.txt"
    path.write_text("BEGIN" + "x" * (OUTPUT_LIMIT * 3) + "MIDDLE-EVIDENCE" + "y" * OUTPUT_LIMIT + "END\n")
    manager = _manager_for(tmp_path, ReadFileTool())
    def call(args):
        resp = asyncio.run(manager(name="read_file_tool", input=args,
                                  ctx=SimpleNamespace(id="c", extra={})))
        assert resp.success, resp.message
        return CapabilityRouter._from_response(ActionCall("c", "read_file_tool", args), resp)
    paged = call({"path": str(path)})
    assert "MIDDLE-EVIDENCE" in paged.output
    assert "omitted inline" in paged.as_message().text
    assert "MIDDLE-EVIDENCE" not in paged.as_message().text
    full = call({"path": str(path), "limit": None})
    assert "MIDDLE-EVIDENCE" in full.as_message().text and "omitted inline" not in full.as_message().text


@pytest.mark.parametrize("args", [{"offset": 0}, {"limit": 0}, {"limit": -1}])
def test_invalid_page_is_explicit_failure(tmp_path, args):
    path = tmp_path / "data.txt"
    path.write_text("exists\n")
    result = asyncio.run(ReadFileTool()(path=str(path), **args))
    assert not result.success and "positive" in result.message
