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

    async def __call__(self, size: int = OUTPUT_LIMIT * 3, ctx=None, **kwargs) -> Response:
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


def test_an_oversized_result_is_referenced_without_splicing_its_text(spill_root, tmp_path):
    """The model receives the exact result as well as its durable locator."""
    import re
    from pathlib import Path

    manager = _manager_for(tmp_path, _Loud())
    resp = asyncio.run(manager(name="loud_tool", input={}, ctx=SimpleNamespace(id="c", extra={})))

    assert resp.success is True
    assert "HEAD-MARKER" in resp.message and "TAIL-MARKER" in resp.message
    assert "omitted inline" not in resp.message

    # The locator is in the message, and what it points at is the whole thing.
    match = re.search(r"saved at `([^`]+)`", resp.message)
    assert match, f"no locator in message: {resp.message[-300:]}"
    saved = Path(match.group(1)).read_text()
    assert len(saved) == OUTPUT_LIMIT * 3 + len("HEAD-MARKER") + len("TAIL-MARKER")
    assert "HEAD-MARKER" in saved and "TAIL-MARKER" in saved
    assert resp.message.startswith(saved)


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
