from types import SimpleNamespace

import pytest

from agentevolver.agent.types import Agent


def _call(name: str):
    return SimpleNamespace(name=name, input={})


@pytest.mark.asyncio
async def test_only_explicitly_read_only_tool_batches_run_in_parallel(monkeypatch):
    class ReadOnly:
        def will_mutate(self, arguments):
            return False

    async def get_info(name):
        return SimpleNamespace(instance=ReadOnly())

    monkeypatch.setattr("agentevolver.tool.tool_manager.get_info", get_info)
    actor = SimpleNamespace(name="meta_agent")

    assert not await Agent._batch_requires_serial(
        actor,
        [_call("read_a"), _call("read_b")],
        {"read_a": ("tool", "read_a"), "read_b": ("tool", "read_b")},
    )


@pytest.mark.asyncio
async def test_unknown_or_mutating_batch_effects_are_serialized(monkeypatch):
    class Unknown:
        def will_mutate(self, arguments):
            return None

    async def get_info(name):
        return SimpleNamespace(instance=Unknown())

    monkeypatch.setattr("agentevolver.tool.tool_manager.get_info", get_info)
    actor = SimpleNamespace(name="meta_agent")

    assert await Agent._batch_requires_serial(
        actor,
        [_call("unknown"), _call("read")],
        {"unknown": ("tool", "unknown"), "read": ("tool", "read")},
    )


@pytest.mark.asyncio
async def test_connector_batch_requires_read_only_annotations(monkeypatch):
    async def get_info(name):
        return SimpleNamespace(action_annotations={
            "lookup": {"readOnlyHint": True},
            "update": {"destructiveHint": False},
        })

    monkeypatch.setattr("agentevolver.connector.connector_manager.get_info", get_info)
    actor = SimpleNamespace(name="meta_agent")

    assert not await Agent._batch_requires_serial(
        actor,
        [_call("records__lookup"), _call("records__lookup_2")],
        {
            "records__lookup": ("connector", "records", "lookup"),
            "records__lookup_2": ("connector", "records", "lookup"),
        },
    )
    assert await Agent._batch_requires_serial(
        actor,
        [_call("records__lookup"), _call("records__update")],
        {
            "records__lookup": ("connector", "records", "lookup"),
            "records__update": ("connector", "records", "update"),
        },
    )
