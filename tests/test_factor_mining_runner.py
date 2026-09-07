"""Launcher boundaries and no-model workflow; never dispatch an LLM agent in tests."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentevolver.data import FactorMarketDataset
from agentevolver.sandbox.filesystem import isolated_command
from examples.run_factor_mining import choose_strategy, parse_args, research, stopping_reason


def test_run_requires_an_explicit_command():
    with pytest.raises(SystemExit):
        parse_args([])
    assert parse_args(["check", "--dataset", "data", "--out", "result"]).command == "check"


def test_objective_stopping_and_validation_selection():
    state = {"factors": {"f": {}}, "remaining_validations": 4, "strategies": {
        "high_train": {"passed": False, "quality": .8},
        "qualified": {"passed": True, "quality": .6}}}
    assert choose_strategy(state) == "qualified"
    assert stopping_reason(state, goal="strategy", round_number=1, max_rounds=3,
                           stagnant=0, no_progress_rounds=2) == "strategy_target_met"
    state["strategies"] = {}
    assert stopping_reason(state, goal="strategy", round_number=2, max_rounds=3,
                           stagnant=2, no_progress_rounds=2) == "no_validation_improvement"


@pytest.mark.asyncio
async def test_no_model_check_runs_through_real_manager(tmp_path, monkeypatch):
    import examples.run_factor_mining as runner

    async def forbidden(*args, **kwargs):
        raise AssertionError("no-model checks must never dispatch an agent")

    monkeypatch.setattr(runner, "dispatch_agent", forbidden)
    dataset = tmp_path / "market"
    FactorMarketDataset.bundle(FactorMarketDataset.synthetic(bars=1800, assets=3), dataset)
    args = argparse.Namespace(dataset=str(dataset), out=str(tmp_path / "check"),
                              config=str(Path(__file__).resolve().parents[1] / "configs/factor_mining.py"),
                              cfg_options=None, resume=False, goal="strategy", command="check")
    await research(args)
    result = json.loads((tmp_path / "check/result.json").read_text())
    run = json.loads((tmp_path / "check/run.json").read_text())
    assert result["score"] == 1
    assert not run["agents_enabled"]
    assert not (tmp_path / "check/runtime").exists()
    args.resume = True
    await research(args)
    assert json.loads((tmp_path / "check/result.json").read_text()) == result


def test_worker_filesystem_has_train_but_no_private_data(tmp_path):
    work = tmp_path / "worker"
    work.mkdir()
    (work / "train.txt").write_text("train")
    private = tmp_path / "hidden_test.txt"
    private.write_text("private test")
    script = ("from pathlib import Path; "
              f"assert Path({str(work / 'train.txt')!r}).read_text() == 'train'; "
              f"assert not Path({str(private)!r}).exists(); "
              f"Path({str(work / 'proof.txt')!r}).write_text('isolated')")
    command = isolated_command([sys.executable, "-c", script], readonly=[sys.prefix],
                                writable=[str(work)], cwd=str(work))
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert (work / "proof.txt").read_text() == "isolated"


def test_specialist_agents_do_not_expose_shell_or_evolution():
    from agentevolver.agent.actor.factor_mining_agent import FactorMiningAgent
    from agentevolver.agent.actor.strategy_mining_agent import StrategyMiningAgent

    for cls in (FactorMiningAgent, StrategyMiningAgent):
        scopes = cls.model_fields["capability_allowlists"].default_factory()
        assert scopes["tool"] == ["done_tool"]
        assert scopes["environment"] == ["factor_mining"]
        assert not scopes["agent"] and not scopes["skill"]


def test_isolated_worker_can_load_config_prompts_and_train_environment_without_agent(tmp_path):
    from mmengine import Config

    from agentevolver.environment.default.factor_mining.research import ResearchProtocol
    from examples.run_factor_mining import worker_command

    workspace, runtime = tmp_path / "workspace", tmp_path / "runtime"
    workspace.mkdir()
    runtime.mkdir()
    (runtime / "home").mkdir()
    panel = FactorMarketDataset.synthetic(bars=100, assets=2)
    FactorMarketDataset.save(panel, workspace / "train")
    (workspace / "study.json").write_text(json.dumps({"frequency": "1h", "symbols": panel.symbols,
                                                     "protocol": ResearchProtocol().model_dump()}))
    config = Config.fromfile(str(Path(__file__).resolve().parents[1] / "configs/factor_mining.py"))
    snapshot = runtime / "config.json"
    snapshot.write_text(json.dumps(config.to_dict()))
    settings = {"dataset": str(tmp_path / "private"), "authority": str(tmp_path / "authority")}
    command = worker_command(runtime / "unused_job.json", workspace, runtime, settings)
    # Run the exact worker filesystem setup, replacing ONLY the program with a
    # config/environment probe. No agent manager initialization or model call.
    probe = f'''import argparse, asyncio
from agentevolver.config import config
from agentevolver.environment.default.factor_mining.environment import FactorMiningEnvironment
from agentevolver.prompt.types import parse_prompt_file
config.initialize({str(snapshot)!r}, argparse.Namespace(), verbose=False)
env = FactorMiningEnvironment(workspace={str(workspace)!r})
assert asyncio.run(env.describe())["bars"] == 100
for name in ("factor_mining_agent", "strategy_mining_agent"):
    parsed = parse_prompt_file({str(Path(__file__).resolve().parents[1] / 'agentevolver/prompt/default')!r} + "/" + name + ".html")
    assert parsed.name == name and "research" in parsed.system_template.lower()
print("config, prompts and train environment load without starting an agent")
'''
    command = command[:command.index("--") + 1] + [sys.executable, "-c", probe]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout[-2000:]
    assert "without starting an agent" in result.stdout
