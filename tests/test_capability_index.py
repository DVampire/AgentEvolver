from types import SimpleNamespace

from agentevolver.agent.capability_index import (
    SEARCH_NAME, catalog, forget, remember_catalog, search, select,
)


def _pair(name, description=""):
    return ({
        "type": "function",
        "function": {
            "name": name, "description": description,
            "parameters": {"type": "object"},
        },
    }, ("tool", name))


def test_small_catalog_stays_eager():
    ctx = SimpleNamespace(extra={})
    pairs = [_pair("done_tool"), _pair("special_tool")]
    chosen, deferred = select(
        pairs, ctx=ctx, agent_name="meta_agent", threshold=3,
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
        pairs, ctx=ctx, agent_name="meta_agent", threshold=2,
    )
    assert deferred
    assert {p[0]["function"]["name"] for p in chosen} == {
        "done_tool", SEARCH_NAME,
    }

    result = search(
        pairs, ctx=ctx, agent_name="meta_agent", query="city weather", limit=1,
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
