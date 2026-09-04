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
