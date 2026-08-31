"""Capability batches run concurrently only when every action is read-only.

Parallelizing an unknown or mutating action can reorder externally visible effects and
make approval checks meaningless. These tests pin the fail-closed classification for
tools, connectors, agents, and mixed batches.
"""

from types import SimpleNamespace

import pytest

from agentevolver.agent.types import Agent


def _call(name: str):
    return SimpleNamespace(name=name, input={})


def _agent_call(name: str, **contract):
    return SimpleNamespace(name=name, input=contract)


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
        return SimpleNamespace(
            action_annotations={
                "lookup": {"readOnlyHint": True},
                "update": {"destructiveHint": False},
            }
        )

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


@pytest.mark.asyncio
async def test_disjoint_isolated_writing_children_can_run_in_parallel():
    actor = SimpleNamespace(name="meta_agent")
    calls = [
        _agent_call("agent__a", read_set=["src/a"], write_set=["src/a"], isolate_worktree=True),
        _agent_call("agent__b", read_set=["src/b"], write_set=["src/b"], isolate_worktree=True),
    ]
    routing = {
        "agent__a": ("agent", "a"),
        "agent__b": ("agent", "b"),
    }
    assert not await Agent._batch_requires_serial(actor, calls, routing)


@pytest.mark.asyncio
async def test_writing_children_serialize_without_isolation_or_on_overlap():
    actor = SimpleNamespace(name="meta_agent")
    routing = {
        "agent__a": ("agent", "a"),
        "agent__b": ("agent", "b"),
    }
    unisolated = [
        _agent_call("agent__a", write_set=["src/a"]),
        _agent_call("agent__b", write_set=["src/b"], isolate_worktree=True),
    ]
    overlapping = [
        _agent_call("agent__a", write_set=["src"], isolate_worktree=True),
        _agent_call("agent__b", read_set=["src/a.py"], write_set=[], isolate_worktree=True),
    ]
    assert await Agent._batch_requires_serial(actor, unisolated, routing)
    assert await Agent._batch_requires_serial(actor, overlapping, routing)


@pytest.mark.asyncio
async def test_children_without_resource_contracts_remain_serial():
    actor = SimpleNamespace(name="meta_agent")
    calls = [_agent_call("agent__a"), _agent_call("agent__b")]
    routing = {"agent__a": ("agent", "a"), "agent__b": ("agent", "b")}
    assert await Agent._batch_requires_serial(actor, calls, routing)
