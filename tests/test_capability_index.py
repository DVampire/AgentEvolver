"""Large capability catalogs are deferred, searchable, and scoped to one run.

Sending every schema on every turn wastes context, while releasing arbitrary matches can
hide required tools or leak selections across sessions. These tests keep the eager and
deferred paths, search behavior, and cleanup boundary explicit.
"""

from types import SimpleNamespace

import pytest

from agentevolver.agent.capability_index import (
    SEARCH_NAME,
    catalog,
    forget,
    remember_catalog,
    search,
    select,
)


def _pair(name, description=""):
    return (
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object"},
            },
        },
        ("tool", name),
    )


def test_small_catalog_stays_eager():
    ctx = SimpleNamespace(extra={})
    pairs = [_pair("done_tool"), _pair("special_tool")]
    chosen, deferred = select(
        pairs,
        ctx=ctx,
        agent_name="meta_agent",
        threshold=3,
    )
    assert chosen == pairs
    assert not deferred


def test_large_catalog_exposes_core_and_search_then_loads_match():
    ctx = SimpleNamespace(extra={})
    pairs = [
        _pair("done_tool"),
        _pair("weather_lookup", "forecast weather by city"),
        _pair("database_query", "query SQL records"),
    ]
    chosen, deferred = select(
        pairs,
        ctx=ctx,
        agent_name="meta_agent",
        threshold=2,
    )
    assert deferred
    assert {p[0]["function"]["name"] for p in chosen} == {
        "done_tool",
        SEARCH_NAME,
    }

    result = search(
        pairs,
        ctx=ctx,
        agent_name="meta_agent",
        query="city weather",
        limit=1,
    )
    assert "weather_lookup" in result
    chosen, _ = select(pairs, ctx=ctx, agent_name="meta_agent", threshold=2)
    assert "weather_lookup" in {p[0]["function"]["name"] for p in chosen}


def test_deferred_catalog_is_released_with_the_agent_run():
    ctx = SimpleNamespace(id="session-1", extra={})
    pairs = [_pair("done_tool"), _pair("weather_lookup")]
    remember_catalog(ctx, "meta_agent", pairs)
    assert catalog(ctx, "meta_agent") == pairs

    forget(ctx, "meta_agent")
    assert catalog(ctx, "meta_agent") == []


def test_empty_search_does_not_load_arbitrary_capabilities():
    ctx = SimpleNamespace(extra={})
    pairs = [_pair("weather_lookup"), _pair("database_query")]

    result = search(pairs, ctx=ctx, agent_name="meta_agent", query="   ")

    assert "non-empty query" in result
    chosen, _ = select(pairs, ctx=ctx, agent_name="meta_agent", threshold=1)
    assert {pair[0]["function"]["name"] for pair in chosen} == {SEARCH_NAME}


def test_zero_match_search_does_not_load_arbitrary_capabilities():
    ctx = SimpleNamespace(extra={})
    pairs = [_pair("weather_lookup"), _pair("database_query")]

    result = search(
        pairs, ctx=ctx, agent_name="meta_agent", query="quantum choreography",
    )

    assert "No capability schemas matched" in result
    chosen, _ = select(pairs, ctx=ctx, agent_name="meta_agent", threshold=1)
    assert {pair[0]["function"]["name"] for pair in chosen} == {SEARCH_NAME}


@pytest.mark.asyncio
async def test_native_catalog_discovery_is_reused_for_the_whole_run(monkeypatch):
    import agentevolver.agent.native_tools as native_tools

    calls = []

    class Manager:
        async def function_callings(self, *_args, **_kwargs):
            calls.append("discover")
            return [_pair("done_tool")]

        async def get_info(self, _name):
            return None

    manager = Manager()
    entry = SimpleNamespace(type="tool", manager=lambda: manager)
    monkeypatch.setattr(native_tools, "MOUNTED_TYPES", (entry,))
    ctx = SimpleNamespace(id="catalog-cache", extra={})
    agent = SimpleNamespace(name="worker", defer_capabilities_after=40)

    first, _ = await native_tools.assemble_native_tools(agent, ctx)
    second, _ = await native_tools.assemble_native_tools(agent, ctx)

    assert [tool.name for tool in first] == ["done_tool"]
    assert [tool.name for tool in second] == ["done_tool"]
    assert calls == ["discover"]
    forget(ctx, "worker")


@pytest.mark.asyncio
async def test_one_broken_catalog_source_does_not_hide_healthy_types(monkeypatch):
    import agentevolver.agent.native_tools as native_tools

    class Broken:
        async def function_callings(self, *_args, **_kwargs):
            raise RuntimeError("bad remote schema")

    class Healthy:
        async def function_callings(self, *_args, **_kwargs):
            return [_pair("done_tool")]

        async def get_info(self, _name):
            return None

    healthy = Healthy()
    entries = (
        SimpleNamespace(type="connector", manager=lambda: Broken()),
        SimpleNamespace(type="tool", manager=lambda: healthy),
    )
    monkeypatch.setattr(native_tools, "MOUNTED_TYPES", entries)
    ctx = SimpleNamespace(id="catalog-isolation", extra={})
    agent = SimpleNamespace(name="worker", defer_capabilities_after=40)

    tools, routing = await native_tools.assemble_native_tools(agent, ctx)

    assert [tool.name for tool in tools] == ["done_tool"]
    assert routing["done_tool"] == ("tool", "done_tool")
    forget(ctx, "worker")
