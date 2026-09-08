"""An oversized tool result is bounded in the prompt but not destroyed.

Clipping alone was the whole policy, and clipping alone throws the dropped middle
away: the agent was told characters were elided and given no way to reach them, so
its only recourse was to re-run the command and be truncated again identically. The
spill store writes the full text first; the excerpt then carries a locator, and
narrowing is a `grep` against a file instead of a second run of the command.
"""

import asyncio
from types import SimpleNamespace

import pytest

from agentevolver.response.types import Response, ResponseType
from agentevolver.tool import spill
from agentevolver.tool.context import ToolContextManager
from agentevolver.tool.spill import SpillRef, SpillSource
from agentevolver.tool.spill.default import LocalSpillStore
from agentevolver.tool.types import OUTPUT_LIMIT, Tool


@pytest.fixture
def spill_root(tmp_path, monkeypatch):
    """Point the whole layout at a temp dir, so spills land under it."""
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    return tmp_path


class _Loud(Tool):
    """A tool whose result is larger than the pipeline will show."""

    name: str = "loud_tool"
    description: str = "Returns a great deal of text."

    async def __call__(self, size: int = OUTPUT_LIMIT * 3,
                       max_output_chars: int = OUTPUT_LIMIT, ctx=None, **kwargs) -> Response:
        body = "HEAD-MARKER" + ("x" * size) + "TAIL-MARKER"
        return Response(type=ResponseType.TOOL, success=True, message=body)


def _manager_for(tmp_path, instance):
    manager = ToolContextManager(base_dir=str(tmp_path))

    async def _fake_get_info(name):
        return SimpleNamespace(version="1.0.0", instance=instance)

    manager.get_info = _fake_get_info
    return manager


# --------------------------------------------------------------------------- #
# The store
# --------------------------------------------------------------------------- #
def test_store_saves_the_text_whole(spill_root):
    """Saving a bounded copy would defeat the point: the excerpt already exists."""
    text = "B" * 250_000
    ref = asyncio.run(
        LocalSpillStore().save_text(text, SpillSource(tool_name="bash_tool"), session_key="s1")
    )

    from pathlib import Path

    assert isinstance(ref, SpillRef)
    assert ref.chars == len(text)
    assert Path(ref.locator).read_text() == text


def test_store_treats_a_suggested_name_as_a_label_not_a_path(spill_root):
    """The name reaches the store from tool arguments, i.e. from something a model wrote."""
    from pathlib import Path

    ref = asyncio.run(
        LocalSpillStore().save_text(
            "x", SpillSource(tool_name="t"), session_key="s1", suggested_name="../../etc/passwd"
        )
    )

    path = Path(ref.locator)
    assert path.name.endswith("passwd")  # kept as a readable label
    assert "etc" not in path.parent.parts  # but it climbed nowhere
    assert spill_root in path.parents


def test_store_writes_private_files(spill_root):
    """A world-readable transcript of an agent's session is a leak, not a convenience."""
    from pathlib import Path

    ref = asyncio.run(
        LocalSpillStore().save_text("x", SpillSource(tool_name="t"), session_key="s1")
    )
    path = Path(ref.locator)

    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_store_separates_sessions(spill_root):
    """One session's artifacts must not land in another's directory."""
    from pathlib import Path

    a = asyncio.run(LocalSpillStore().save_text("a", SpillSource(tool_name="t"), session_key="s1"))
    b = asyncio.run(LocalSpillStore().save_text("b", SpillSource(tool_name="t"), session_key="s2"))

    assert Path(a.locator).parent != Path(b.locator).parent


def test_a_storage_failure_is_absorbed(spill_root, monkeypatch):
    """A full disk loses the transcript. It must not also lose the command's result."""

    class _Broken(LocalSpillStore):
        async def save_text(self, *args, **kwargs):
            raise OSError("No space left on device")

    spill.use_store(_Broken())
    try:
        assert (
            asyncio.run(spill.save_text("x" * 100, SpillSource(tool_name="t"), session_key="s"))
            is None
        )
    finally:
        spill.use_store(None)  # back to the default on the next call


# --------------------------------------------------------------------------- #
# The policy, applied in the dispatch funnel
# --------------------------------------------------------------------------- #
def test_small_results_are_untouched(spill_root, tmp_path):
    manager = _manager_for(tmp_path, _Loud())
    resp = asyncio.run(
        manager(name="loud_tool", input={"size": 10}, ctx=SimpleNamespace(id="c", extra={}))
    )

    assert resp.message.startswith("HEAD-MARKER")
    assert "characters elided" not in resp.message
    assert "saved at `" not in resp.message


def test_an_oversized_result_keeps_canonical_text_and_bounds_only_the_model_view(spill_root, tmp_path):
    """Programs/Trace keep full output; the Agent gets a retrievable excerpt."""
    from pathlib import Path
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.agent.loop.decision import ActionCall

    manager = _manager_for(tmp_path, _Loud())
    resp = asyncio.run(manager(name="loud_tool", input={}, ctx=SimpleNamespace(id="c", extra={})))

    assert resp.success is True
    assert "HEAD-MARKER" in resp.message and "TAIL-MARKER" in resp.message
    assert "omitted inline" not in resp.message

    # The locator accompanies the model view and points to the whole result.
    archive = resp.extra["output_archive"]
    saved = Path(archive["locator"]).read_text()
    assert len(saved) == OUTPUT_LIMIT * 3 + len("HEAD-MARKER") + len("TAIL-MARKER")
    assert "HEAD-MARKER" in saved and "TAIL-MARKER" in saved
    assert resp.message == saved
    action = CapabilityRouter._from_response(ActionCall("c", "loud_tool"), resp)
    assert action.output == saved
    shown = action.as_message().text
    assert len(shown) < OUTPUT_LIMIT + 1000
    assert "HEAD-MARKER" in shown and "TAIL-MARKER" in shown
    assert "omitted inline" in shown and archive["locator"] in shown


@pytest.mark.parametrize("success", [True, False])
def test_structured_canonical_output_and_error_status_survive_excerpting(spill_root, tmp_path, success):
    import json
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.agent.loop.decision import ActionCall

    body = json.dumps({"diagnostics": "X" * (OUTPUT_LIMIT * 2), "exit_code": 7})
    class _Structured(_Loud):
        async def __call__(self, **kwargs):
            return Response(type=ResponseType.TOOL, success=success, message=body,
                            data={"exit_code": 7})
    resp = asyncio.run(_manager_for(tmp_path, _Structured())(
        name="loud_tool", input={}, ctx=SimpleNamespace(id="c", extra={})))
    assert json.loads(resp.message)["exit_code"] == 7
    action = CapabilityRouter._from_response(ActionCall("c", "loud_tool"), resp)
    assert action.ok == success
    assert action.extra["exit_code"] == 7
    assert action.as_message().is_error == (not success)
    assert "omitted inline" in action.as_message().text
    assert json.loads(action.output if success else action.error)["exit_code"] == 7


def test_explicit_full_output_is_not_truncated_again(spill_root, tmp_path):
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.agent.loop.decision import ActionCall

    resp = asyncio.run(_manager_for(tmp_path, _Loud())(
        name="loud_tool", input={"max_output_chars": 0}))
    shown = CapabilityRouter._from_response(ActionCall("c", "loud_tool"), resp).as_message().text
    assert resp.message in shown
    assert "omitted inline" not in shown


@pytest.mark.asyncio
async def test_batch_dispatch_can_parse_full_result_after_model_excerpting(spill_root, tmp_path):
    import json
    from agentevolver.agent.loop import ActionCall, Agent, ToolRouter
    from agentevolver.agent.loop.executor import ActionExecutor
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.code import BATCH_CALL_TOOL

    body = json.dumps({"payload": "x" * (OUTPUT_LIMIT * 3), "answer": 42})

    class _JSONTool(_Loud):
        async def __call__(self, **kwargs):
            return Response(type=ResponseType.TOOL, success=True, message=body)

    manager = _manager_for(tmp_path, _JSONTool())

    class Router(ToolRouter):
        async def invoke(self, call, **kwargs):
            response = await manager(name=call.name, input=call.args)
            result = CapabilityRouter._from_response(call, response)
            assert "omitted inline" in result.as_message().text
            return result

    routes = {name: ("tool", name) for name in (BATCH_CALL_TOOL, "loud_tool")}
    bridge = ActionExecutor(Router())._bridge(
        ActionCall("program", BATCH_CALL_TOOL), Agent(name="probe"), None, routes, {})
    result = await bridge.call("loud_tool", {})
    assert json.loads(result) == json.loads(body)


def test_done_deliverable_is_not_excerpted(spill_root, tmp_path):
    from agentevolver.agent.loop.router import CapabilityRouter
    from agentevolver.agent.loop.decision import ActionCall

    class _Final(_Loud):
        async def __call__(self, **kwargs):
            return Response(type=ResponseType.TOOL, success=True, message="Z" * (OUTPUT_LIMIT * 2),
                            data={"done": True})
    resp = asyncio.run(_manager_for(tmp_path, _Final())(name="loud_tool", input={}))
    action = CapabilityRouter._from_response(ActionCall("c", "loud_tool"), resp)
    assert action.final and action.output == resp.message
    assert "model_observation" not in resp.extra


def test_bound_session_archive_uses_log_path_and_can_be_read(tmp_path, monkeypatch):
    from pathlib import Path
    from agentevolver.paths.server import PathManagerServer
    from agentevolver.paths import P
    import agentevolver.tool.spill.default.local as local
    import agentevolver.sandbox.project as project

    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    paths = PathManagerServer()
    paths.bind_session("audit", "run")
    monkeypatch.setattr(local, "path_manager", paths)
    monkeypatch.setattr(project, "path_manager", paths)
    ref = asyncio.run(LocalSpillStore().save_text("exact evidence", SpillSource(tool_name="x"), session_key="run"))
    assert Path(ref.locator).is_relative_to(paths.get(P.SESSION_LOG))
    assert project.check_session_path(path=ref.locator, write=False) is None
    assert Path(ref.locator).read_text() == "exact evidence"


def test_a_failed_spill_still_returns_the_complete_result(spill_root, tmp_path):
    """Filing the transcript failing is not the command failing."""

    class _Broken(LocalSpillStore):
        async def save_text(self, *args, **kwargs):
            raise OSError("backend down")

    spill.use_store(_Broken())
    try:
        manager = _manager_for(tmp_path, _Loud())
        resp = asyncio.run(
            manager(name="loud_tool", input={}, ctx=SimpleNamespace(id="c", extra={}))
        )
    finally:
        spill.use_store(None)  # back to the default on the next call

    assert resp.success is True  # the tool did its job
    assert len(resp.message) == OUTPUT_LIMIT * 3 + len("HEAD-MARKER") + len("TAIL-MARKER")
    assert "characters elided" not in resp.message
    assert "saved at `" not in resp.message  # honest about having no locator
