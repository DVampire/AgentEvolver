"""Continuation preserves authored work and planning reminders detect stale content."""
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.agent.actor.game_builder_agent import GameBuilderAgent
from examples.run_game_development_demo import seed_game_session
from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.plan.server import plan_manager, plan_path
from agentevolver.plan.types import PlanMode


def test_continuation_preserves_source_saves_and_design_without_reusing_cache(tmp_path):
    source = tmp_path / "old"
    source.mkdir()
    (source / "session.json").write_text("{}")
    game = source / "workspace/game"
    game.mkdir(parents=True)
    (game / "project.godot").write_text("config_version=5")
    (game / ".godot").mkdir()
    (game / ".godot/stale-cache").write_text("old absolute paths")
    save = source / "workspace/.godot-agent/userdata"
    save.mkdir(parents=True)
    (save / "save.json").write_text('{"chapter":1}')
    (source / "plan").mkdir()
    previous_plan = f"Design: companion remembers choices.\nProject: {game}\nVerification pending."
    (source / "plan/plan.md").write_text(previous_plan)
    (source / "plan/story.md").write_text("A rescue changes the harbor alliance.")
    workspace, plan = tmp_path / "new/workspace", tmp_path / "new/plan"
    workspace.mkdir(parents=True)
    plan.mkdir()
    receipt = seed_game_session(str(source), workspace, plan)
    assert (workspace / "game/project.godot").read_text() == "config_version=5"
    assert not (workspace / "game/.godot").exists()
    assert (workspace / ".godot-agent/userdata/save.json").read_text() == '{"chapter":1}'
    assert str(workspace / "game") in (plan / "plan.md").read_text()
    assert (plan / "story.md").is_file()
    assert (source / "plan/plan.md").read_text() == previous_plan
    assert receipt["requires_plan_reconciliation"]
    with pytest.raises(ValueError, match="must be empty"):
        seed_game_session(str(source), workspace, plan)


def test_continuation_refuses_live_source(tmp_path):
    from agentevolver.visual.run.server import process_start

    source = tmp_path / "old"
    (source / "workspace").mkdir(parents=True)
    (source / "session.json").write_text("{}")
    (source / "log").mkdir()
    (source / "log/run_monitor.json").write_text(json.dumps({
        "launcher_pid": os.getpid(), "launcher_start": process_start(os.getpid()),
    }))
    with pytest.raises(ValueError, match="Stop the source"):
        seed_game_session(str(source), tmp_path / "new/workspace", tmp_path / "new/plan")


def test_continue_cli_prepares_artifacts_without_agent_configuration(tmp_path, monkeypatch):
    from examples import run_game_development_demo as entry, run_meta_agent as runner

    source = tmp_path / "old"
    (source / "workspace/game").mkdir(parents=True)
    (source / "session.json").write_text("{}")
    (source / "workspace/game/project.godot").write_text("config_version=5")
    (source / "plan").mkdir()
    (source / "plan/plan.md").write_text("ART: replace placeholders before new quests")
    workspace, plan = tmp_path / "new/workspace", tmp_path / "new/plan"
    workspace.mkdir(parents=True)
    plan.mkdir()
    prepared = []

    async def launch_stub(*, prepare_session):
        import sys
        assert not any("game_builder_agent.continue_from=" in value for value in sys.argv)
        prepare_session(workspace, plan)
        prepared.append((workspace / "game/project.godot").read_text())

    monkeypatch.setattr(runner, "run_with_lifecycle", launch_stub)
    entry.launch(entry.parse_args(["--continue-from", str(source)]))
    assert prepared == ["config_version=5"]
    assert (plan / "plan.md").read_text().startswith("ART:")


@pytest.mark.asyncio
async def test_failed_preparation_stops_before_manager_initialization(tmp_path, monkeypatch):
    from examples import run_meta_agent as runner

    roots = SimpleNamespace(workspace_root=tmp_path / "workspace", plan_root=tmp_path / "plan")
    monkeypatch.setattr(runner, "parse_args", lambda: SimpleNamespace(config="unused"))
    monkeypatch.setattr(runner, "config", SimpleNamespace(initialize=lambda **kw: None, extension_root="unused"))
    monkeypatch.setattr(runner, "ensure_session_sandbox", lambda *a, **kw: roots)
    monkeypatch.setattr(runner, "bind_session_roots", lambda *a: None)
    initialize = AsyncMock()
    monkeypatch.setattr(runner.version_manager, "initialize", initialize)

    def cannot_prepare(workspace, plan):
        assert (workspace, plan) == (roots.workspace_root, roots.plan_root)
        raise ValueError("source still running")

    with pytest.raises(ValueError, match="source still running"):
        await runner.main(prepare_session=cannot_prepare)
    initialize.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_reminder_requires_content_change_and_respects_off(bound_session, monkeypatch):
    monkeypatch.setattr(MetaAgent, "on_step", AsyncMock(return_value="base note"))
    agent = GameBuilderAgent(use_plan=True)
    agent.ctx = SimpleNamespace(id=bound_session["workspace"].parent.name)
    path = plan_path(agent.ctx.id)
    assert "game-plan-update-required" in await agent.on_step(1)
    path.write_text("# Design\nChapter: rescue. Loop: explore → bond → consequence.")
    assert await agent.on_step(2) == "base note"
    path.touch()  # Touching alone must not pretend progress was recorded.
    assert "game-plan-update-required" in await agent.on_step(5)
    path.write_text(path.read_text() + "\nImplemented rescue trigger; import failed; fix syntax next.")
    assert await agent.on_step(6) == "base note"
    plan_manager.set_mode(agent.ctx.id, PlanMode.OFF)
    try:
        assert await agent.on_step(20) == "base note"
    finally:
        plan_manager.set_mode(agent.ctx.id, PlanMode.AUTO)
