"""Examples own agent execution; benchmark manager owns grading and task facilities."""

import ast
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentevolver.benchmark import BenchmarkManager

NAMES = ["programbench", "swebench_pro", "swebench_verified"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("inner", [False, True])
async def test_example_configures_and_dispatches_its_own_run(name, inner, monkeypatch, tmp_path):
    entry = importlib.import_module("examples.run_" + name)
    init_calls = []
    fake_config = SimpleNamespace(initialize=lambda **kwargs: init_calls.append(kwargs))
    monkeypatch.setattr(entry, "config", fake_config)
    monkeypatch.setattr(entry, "logger", SimpleNamespace(initialize=lambda **kwargs: None))
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: True)
    launcher, worker = AsyncMock(return_value=9), AsyncMock(return_value=3)
    monkeypatch.setattr(entry, "run_launcher", launcher)
    monkeypatch.setattr(entry, "run_inner", worker)
    argv = [
        "--start",
        "0",
        "--end",
        "1",
        "--out",
        str(tmp_path),
        "--concurrency",
        "2",
        "--no-monitor",
        "--cfg-options",
        "model_name=test/model",
    ]
    if inner:
        argv += ["--instance-json", '{"instance_id":"example"}']
    assert await entry.main(argv) == (3 if inner else 9)
    call = worker.call_args if inner else launcher.call_args
    (launcher if inner else worker).assert_not_called()
    args = call.args[0]
    assert args.start == 0 and args.end == 1 and args.concurrency == 2
    assert args.user_cfg_options == {"model_name": "test/model"}
    assert init_calls == [{"config_path": args.config, "args": args}]


def test_execution_lives_in_examples_and_benchmark_has_no_agent_dispatch():
    root = Path(__file__).resolve().parents[1]
    assert not hasattr(BenchmarkManager, "run")
    assert not (root / "agentevolver/benchmark/_execution").exists()
    assert not (root / "agentevolver/utils/benchmark_runner.py").exists()
    assert not (root / "agentevolver/benchmark/_facilities.py").exists()
    for name in NAMES:
        source = (root / f"examples/run_{name}.py").read_text()
        tree = ast.parse(source)
        functions = {
            n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert {"main", "run_inner", "run_launcher"} <= functions.keys()
        worker = ast.get_source_segment(source, functions["run_inner"])
        assert "await agent_manager.initialize(" in worker
        assert "task_manager.submit(" in worker
        assert "benchmark_manager.eval(" in source
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any(
            m
            and (
                m.startswith("examples.")
                or "_execution" in m
                or "_facilities" in m
                or "benchmark_runner" in m
            )
            for m in imports
        )
    for source in (root / "agentevolver/benchmark").rglob("*.py"):
        text = source.read_text()
        assert "agent_manager.initialize(" not in text
        assert "task_manager.submit(" not in text


@pytest.mark.parametrize("name", NAMES)
def test_cli_rejects_zero_worker_concurrency(name):
    entry = importlib.import_module("examples.run_" + name)
    with pytest.raises(SystemExit) as result:
        entry.parse_args(["--start", "0", "--end", "1", "--concurrency", "0"])
    assert result.value.code == 2
