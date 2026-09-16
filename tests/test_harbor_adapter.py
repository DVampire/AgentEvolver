"""Running this framework's agents on Harbor benchmarks, scored by Harbor.

Harbor inverts the usual direction: it builds the task container, calls an agent with an
instruction and an environment, and afterwards runs the task's own verifier inside that
container. Taking its side of that deal is what makes a score comparable — provisioning
our own container would score a setup the leaderboard never ran, which is the failure
`deep-swe` 1.1 moved grading into an isolated container to prevent.
"""

import subprocess

import pytest

from agentevolver.sandbox.default.harbor import HarborSandbox


class _Environment:
    """A stand-in for Harbor's BaseEnvironment that really executes, on this host."""

    working_dir = "/tmp"

    def __init__(self):
        self.calls = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.calls.append({"command": command, "cwd": cwd, "timeout_sec": timeout_sec})
        done = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True)
        return type("ExecResult", (), {
            "stdout": done.stdout, "stderr": done.stderr, "return_code": done.returncode,
        })()

    async def upload_file(self, source_path, target_path):
        import shutil

        shutil.copy(source_path, target_path)

    async def download_file(self, source_path, target_path):
        import shutil

        shutil.copy(source_path, target_path)


@pytest.mark.asyncio
async def test_commands_run_in_harbors_container():
    sandbox = HarborSandbox(environment=_Environment())
    result = await sandbox.run_command("echo hello")
    assert result.success
    assert result.stdout.strip() == "hello"


@pytest.mark.asyncio
async def test_a_failing_command_is_a_result_not_an_exception():
    """Tools read ExecResult. A raised exec would end a graded trial on something the
    agent could have read and worked around."""
    sandbox = HarborSandbox(environment=_Environment())
    result = await sandbox.run_command("exit 3")
    assert not result.success
    assert result.exit_code == 3


@pytest.mark.asyncio
async def test_an_unreachable_environment_is_also_a_result():
    class _Broken(_Environment):
        async def exec(self, *a, **k):
            raise ConnectionError("container went away")

    result = await HarborSandbox(environment=_Broken()).run_command("true")
    assert not result.success
    assert "ConnectionError" in (result.error or "")


@pytest.mark.asyncio
async def test_files_survive_content_a_shell_would_mangle(tmp_path):
    """The base class writes files by piping base64 through a shell; this backend uses
    Harbor's own transfer, and must handle what the shell path was protecting against."""
    sandbox = HarborSandbox(environment=_Environment())
    target = str(tmp_path / "x.txt")
    payload = "中文 & 'single' \"double\" $VAR `cmd`"
    await sandbox.write_file(target, payload)
    assert await sandbox.read_file(target) == payload


@pytest.mark.asyncio
async def test_the_container_outlives_the_agent():
    """Harbor stops the container after its verifier has run. Destroying it when the
    agent finishes would delete the filesystem the reward is computed from."""
    environment = _Environment()
    sandbox = HarborSandbox(environment=environment)
    await sandbox.start()
    await sandbox.destroy()
    assert environment.calls == []


def test_wrapping_nothing_is_refused():
    """The environment is the whole point; a sandbox without one would fail later, in a
    tool, as something that looks like a task failure."""
    with pytest.raises(ValueError, match="environment"):
        HarborSandbox()


def test_harbor_can_find_the_agent_by_import_path():
    """Harbor loads an external agent as `module:Class`, which is what makes this work
    without forking Harbor. The path it computes must be the one that imports."""
    import importlib

    from agentevolver.benchmark.harbor import AgentEvolverAgent

    path = AgentEvolverAgent.import_path()
    module_name, _, class_name = path.partition(":")
    assert getattr(importlib.import_module(module_name), class_name) is AgentEvolverAgent


def test_the_agent_satisfies_harbors_contract():
    from harbor.agents.base import BaseAgent

    from agentevolver.benchmark.harbor import AgentEvolverAgent

    assert issubclass(AgentEvolverAgent, BaseAgent)
    assert not getattr(AgentEvolverAgent, "__abstractmethods__", ())
    assert AgentEvolverAgent.name() == "agentevolver"


def test_usage_is_absent_rather_than_zero_when_unmeasured(monkeypatch):
    """`trace_manager.events` returns empty when this process is not holding the log —
    not when the session did nothing. Zeros would put a free trial on Harbor's record."""
    from agentevolver.benchmark.harbor.agent import AgentEvolverAgent
    from agentevolver.trace import trace_manager

    monkeypatch.setattr(trace_manager, "events", lambda session_id=None: [])
    agent = AgentEvolverAgent.__new__(AgentEvolverAgent)
    agent._session = None
    assert agent._collect_usage() == {}


@pytest.mark.asyncio
async def test_no_working_directory_is_invented():
    """Harbor resolves a task's working directory itself and exposes it nowhere, so a
    default here is a guess. An earlier version answered `/app` — where SWE-bench Pro's
    images keep their tree, and wrong for any task built otherwise."""
    environment = _Environment()
    sandbox = HarborSandbox(environment=environment)

    assert sandbox.container_workspace is None
    await sandbox.run_command("true")
    assert environment.calls[-1]["cwd"] is None


@pytest.mark.asyncio
async def test_a_caller_can_still_name_a_directory():
    """Not passing a default is not the same as refusing one that was asked for."""
    environment = _Environment()
    await HarborSandbox(environment=environment).run_command("true", workspace_root="/tmp")
    assert environment.calls[-1]["cwd"] == "/tmp"


def test_the_step_budget_is_named_the_way_the_agent_declares_it():
    """It used to be passed as a call argument to agent_manager, which merges kwargs into
    the agent's payload — it matched no parameter and was dropped without a word, so every
    trial ran at whatever the config file said. The field is `max_step`, singular."""
    from agentevolver.agent.loop.agent import Agent
    from agentevolver.benchmark.harbor.agent import AgentEvolverAgent

    assert "max_step" in Agent.model_fields
    assert "max_steps" not in Agent.model_fields

    agent = AgentEvolverAgent.__new__(AgentEvolverAgent)
    agent._agent_name = "meta_agent"
    agent._step_budget = 7
    agent._extension_root = ""
    agent.model_name = None
    assert agent.config_overrides() == {"meta_agent.max_step": 7}


def test_the_model_harbor_named_beats_the_config():
    """That name is part of what a leaderboard row means, so the flag cannot be a lie."""
    from agentevolver.benchmark.harbor.agent import AgentEvolverAgent

    agent = AgentEvolverAgent.__new__(AgentEvolverAgent)
    agent._agent_name = "meta_agent"
    agent._step_budget = 7
    agent._extension_root = "/tmp/ext"
    agent.model_name = "llm_hub/claude-opus-5"

    assert agent.config_overrides() == {
        "meta_agent.max_step": 7,
        "extension_root": "/tmp/ext",
        "model_name": "llm_hub/claude-opus-5",
        "meta_agent.model_name": "llm_hub/claude-opus-5",
    }


def test_bring_up_points_the_extension_manager_at_the_configured_root():
    """`config.extension_root` can name a writable tree precisely because the repository's
    own `extension/` may not be — a shared checkout can have it owned by another account.
    A caller that set the config but forgot to pass it on still wrote to the default and
    died on a manifest it could not open, so the line lives beside the initialization it
    governs rather than in each caller."""
    source = open("agentevolver/session/bringup.py").read()
    set_at = source.index("extension_manager.set_base_dir(config.extension_root)")
    init_at = source.index("await extension_manager.initialize()")
    assert set_at < init_at, "the root must be set before the manager reads it"


def test_the_adapter_configures_through_the_config_override_channel():
    """Calling `config.initialize` without its required `args` failed outright; mutating
    config attributes afterwards would depend on the config's shape and land after the
    agent sections are processed."""
    import inspect

    from agentevolver.benchmark.harbor.agent import AgentEvolverAgent
    from agentevolver.config import config

    assert "args" in inspect.signature(config.initialize).parameters
    source = inspect.getsource(AgentEvolverAgent.setup)
    assert "args=Namespace(cfg_options=self.config_overrides())" in source
