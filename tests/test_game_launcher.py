"""Game experiments start independently and retain the shared planning lifecycle."""
import sys
from unittest.mock import AsyncMock

import pytest

from agentevolver.agent.actor.game_builder_agent import GameBuilderAgent
from agentevolver.agent.actor.meta_agent import MetaAgent
from examples import run_game_development_demo as entry


def test_game_launcher_rejects_cross_experiment_copying():
    with pytest.raises(SystemExit) as error:
        entry.parse_args(["--continue-from", "/old/session"])
    assert error.value.code == 2


def test_game_launcher_uses_shared_fresh_lifecycle(monkeypatch):
    from examples import run_meta_agent as runner

    calls = []

    async def launch_stub():
        args = runner.parse_args()
        calls.append(args)

    monkeypatch.setattr(runner, "run_with_lifecycle", launch_stub)
    original = sys.argv
    entry.launch(entry.parse_args([]))
    assert sys.argv is original
    assert len(calls) == 1
    assert calls[0].agent_name == "game_builder_agent"
    assert len(calls[0].attach) == 1
    assert calls[0].plan_mode == "auto"


@pytest.mark.asyncio
async def test_game_builder_keeps_shared_plan_step_lifecycle(monkeypatch):
    shared = AsyncMock(return_value="shared plan review gate")
    monkeypatch.setattr(MetaAgent, "on_step", shared)
    agent = GameBuilderAgent(use_plan=True)
    for step in (1, 4, 20):
        assert await agent.on_step(step) == "shared plan review gate"
    assert shared.await_count == 3
