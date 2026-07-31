import argparse
import os
import sys
from pathlib import Path

import pytest

root = str(Path(__file__).resolve().parents[1])
sys.path.append(root)

from agentevolver.config import config


def test_programbench_agent_config_loads_expected_roster():
    config.initialize(
        config_path=os.path.join(root, "configs", "programbench_agent.py"),
        args=argparse.Namespace(),
        verbose=False,
    )
    assert "meta_agent" in config.agent_names
    assert "code_agent" in config.agent_names
    assert "general_agent" in config.agent_names
    assert "reviewer_agent" in config.agent_names
    # monitor_agent is deliberately excluded — it spawns its own bash subprocess
    # directly, bypassing the Docker sandbox bash_tool routes through.
    assert "monitor_agent" not in config.agent_names
    # The config carries the self-evolution roster outright (like meta_agent.py);
    # --no-evolve strips it back out via resolve_roster().
    assert "tool_optimize_agent" in config.agent_names
    assert "agent_generate_agent" in config.agent_names
    assert "skill_evaluate_agent" in config.agent_names
    assert "evolution_tool" in config.tool_names
    assert "self_evolving_skill" in config.skill_names
    # Out of scope for a reconstruction run: nothing to evolve for environments
    # or connectors, and swapping the memory system would change the measurement.
    for absent in ("environment_generate_agent", "memory_generate_agent", "connector_evaluate_agent"):
        assert absent not in config.agent_names
    for absent in ("environment_creator_skill", "memory_creator_skill", "connector_creator_skill"):
        assert absent not in config.skill_names
    assert "bash_tool" in config.tool_names
    # None of these check get_current_sandbox() — they'd silently operate on the
    # host workspace instead of the container once a sandbox is bound.
    assert "read_file_tool" not in config.tool_names
    assert "write_file_tool" not in config.tool_names
    assert "edit_file_tool" not in config.tool_names
    assert "list_dir_tool" not in config.tool_names
    assert "git_tool" not in config.tool_names
    # Verification methodology is not optional: an empty skill list scored 53 on
    # cmatrix because the agent never checked its build against the reference
    # binary. These stay loaded even under --no-evolve.
    for required in ("verify_skill", "test_driven_development_skill",
                     "debugging_and_error_recovery_skill"):
        assert required in config.skill_names
    assert not set(rp.EVOLVE_SKILL_NAMES) & {
        "verify_skill", "test_driven_development_skill",
        "debugging_and_error_recovery_skill", "incremental_implementation_skill",
        "source_driven_development_skill",
    }, "verification skills must survive --no-evolve"
    # Still lean — no document, science or unrelated workflow skills.
    assert "docx_skill" not in config.skill_names
    assert "observability_and_instrumentation_skill" not in config.skill_names
    assert config.connector_names == []
    assert config.env_names == []


sys.path.append(str(Path(root) / "examples"))

import run_programbench as rp  # noqa: E402


def test_select_instances_by_task_ids():
    instances = [
        {"instance_id": "a", "repository": "repo-a"},
        {"instance_id": "b", "repository": "repo-b"},
        {"instance_id": "c", "repository": "repo-c"},
    ]
    selected, warnings = rp.select_instances(instances, task_ids=["c", "a"])
    assert [i["instance_id"] for i in selected] == ["c", "a"]
    assert warnings == []


def test_select_instances_by_task_ids_skips_unknown():
    instances = [{"instance_id": "a"}, {"instance_id": "b"}]
    selected, warnings = rp.select_instances(instances, task_ids=["a", "does-not-exist"])
    assert [i["instance_id"] for i in selected] == ["a"]
    assert warnings == ["unknown task id(s) skipped: ['does-not-exist']"]


def test_select_instances_by_range():
    instances = [{"instance_id": str(i)} for i in range(10)]
    selected, warnings = rp.select_instances(instances, start=2, end=5)
    assert [i["instance_id"] for i in selected] == ["2", "3", "4"]
    assert warnings == []


def test_select_instances_task_ids_take_precedence_over_range():
    instances = [{"instance_id": "a"}, {"instance_id": "b"}]
    selected, warnings = rp.select_instances(instances, task_ids=["b"], start=0, end=1)
    assert [i["instance_id"] for i in selected] == ["b"]
    assert warnings == ["--start/--end ignored because --task-ids was given"]


def test_select_instances_requires_a_selector():
    with pytest.raises(ValueError):
        rp.select_instances([{"instance_id": "a"}])


def test_build_task_content_includes_system_prompt_and_fields():
    instance = {
        "repository": "abishekvashok/cmatrix",
        "language": "c",
        "image_name": "programbench/abishekvashok_1776_cmatrix.5c082c6",
        "commit": "5c082c6",
    }
    content = rp.build_task_content(instance)
    assert rp.SYSTEM_PROMPT.strip() in content
    assert "abishekvashok/cmatrix" in content
    assert "language: c" in content
    assert "./executable" in content
    assert "./compile.sh" in content


def test_resolve_roster_off_leaves_a_base_without_addons_alone():
    agents, tools, skills = rp.resolve_roster(
        ["meta_agent"], ["bash_tool"], [], evolve=False,
    )
    assert agents == ["meta_agent"]
    assert tools == ["bash_tool"]
    assert skills == []


def test_resolve_roster_off_strips_addons_the_config_already_lists():
    agents, tools, skills = rp.resolve_roster(
        ["meta_agent", "tool_optimize_agent"],
        ["bash_tool", "evolution_tool"],
        ["self_evolving_skill", "tool_creator_skill"],
        evolve=False,
    )
    assert agents == ["meta_agent"]
    assert tools == ["bash_tool"]
    assert skills == []


def test_resolve_roster_on_adds_triads():
    agents, tools, skills = rp.resolve_roster(
        ["meta_agent"], ["bash_tool"], [], evolve=True,
    )
    assert "tool_optimize_agent" in agents
    assert "skill_generate_agent" in agents
    assert len(agents) == 1 + len(rp.EVOLVE_AGENT_NAMES)
    assert "evolution_tool" in tools
    assert "self_evolving_skill" in skills
    assert "agent_creator_skill" in skills


def test_resolve_roster_on_does_not_duplicate_what_the_config_lists():
    agents, _, _ = rp.resolve_roster(
        ["meta_agent"] + rp.EVOLVE_AGENT_NAMES, [], [], evolve=True,
    )
    assert len(agents) == 1 + len(rp.EVOLVE_AGENT_NAMES)


def test_resolve_roster_scope_excludes_environment_memory_connector():
    for name in rp.EVOLVE_AGENT_NAMES:
        assert not name.startswith(("environment_", "memory_", "connector_"))
    for name in rp.EVOLVE_SKILL_NAMES:
        assert name not in (
            "environment_creator_skill", "memory_creator_skill", "connector_creator_skill",
        )


def test_resolve_roster_does_not_mutate_input_lists():
    base_agents = ["meta_agent"]
    rp.resolve_roster(base_agents, [], [], evolve=True)
    assert base_agents == ["meta_agent"]


def test_parse_args_requires_a_selector():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py"]
    try:
        with pytest.raises(SystemExit):
            rp.parse_args()
    finally:
        sys.argv = old_argv


def test_parse_args_evolve_defaults_to_true():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py", "--start", "0", "--end", "1"]
    try:
        args = rp.parse_args()
    finally:
        sys.argv = old_argv
    assert args.evolve is True


def test_parse_args_no_evolve_flag():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py", "--start", "0", "--end", "1", "--no-evolve"]
    try:
        args = rp.parse_args()
    finally:
        sys.argv = old_argv
    assert args.evolve is False


def test_parse_args_task_ids():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py", "--task-ids", "a,b, c"]
    try:
        args = rp.parse_args()
    finally:
        sys.argv = old_argv
    assert args.task_ids == "a,b, c"


class _FakeExecResult:
    def __init__(self, success):
        self.success = success

    def as_message(self):
        return "ok" if self.success else "tar: command failed"


class _FakeSandbox:
    """Minimal stand-in for a real OpenSandbox handle — no Docker involved."""

    def __init__(self, tar_bytes=b"fake-tar-bytes", command_success=True):
        self._tar_bytes = tar_bytes
        self._command_success = command_success
        self.commands_run = []

    async def run_command(self, command):
        self.commands_run.append(command)
        return _FakeExecResult(self._command_success)

    async def read_bytes(self, path):
        assert path == "/tmp/submission.tar.gz"
        return self._tar_bytes


@pytest.mark.asyncio
async def test_extract_submission_writes_tar_to_dest_dir(tmp_path):
    # extract_submission pulls the tarball out AND unpacks it host-side for
    # inspection, so the fake sandbox must return a real gzip tar (not a raw
    # placeholder) or the unpack step raises tarfile.ReadError.
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        member = tarfile.TarInfo(name="hello.txt")
        payload = b"hello-tar"
        member.size = len(payload)
        tf.addfile(member, io.BytesIO(payload))
    tar_bytes = buf.getvalue()

    sandbox = _FakeSandbox(tar_bytes=tar_bytes)
    dest_dir = str(tmp_path / "workspace")

    result_path = await rp.extract_submission(sandbox, dest_dir)

    assert result_path == str(Path(dest_dir) / "submission.tar.gz")
    with open(result_path, "rb") as f:
        assert f.read() == tar_bytes
    assert sandbox.commands_run == ["tar -czf /tmp/submission.tar.gz -C /workspace . 2>&1"]
    # The tarball is also unpacked into dest_dir/submission/ for direct inspection.
    unpacked = Path(dest_dir) / "submission" / "hello.txt"
    assert unpacked.read_bytes() == b"hello-tar"


@pytest.mark.asyncio
async def test_extract_submission_raises_on_tar_failure():
    sandbox = _FakeSandbox(command_success=False)

    with pytest.raises(RuntimeError):
        await rp.extract_submission(sandbox, "/tmp/wherever")


# --- prompt roots under a peer sandbox -------------------------------------
# Regression test for a live ProgramBench failure: the prompt told the agent its
# workspace was /workspace (correct) while project_root/log_root still named host
# paths under output/<owner>/sessions/<id>. The agent trusted project_root, looked
# for the task files under its `workspace/` subdirectory, found nothing, and fell
# back to `find / -name ...` — which consumed bash_tool's entire 600s timeout.

from agentevolver.agent import types as agent_types  # noqa: E402


class _PeerSandbox:
    container_workspace = "/workspace"


class _HostSandbox:
    """A sandbox that runs on host paths directly — no remapping."""
    container_workspace = None


class _Ctx:
    def __init__(self, extra):
        self.extra = extra


def test_sandbox_of_only_matches_container_backed_peers():
    assert agent_types._sandbox_of(_Ctx({"sandbox": _PeerSandbox()})) is not None
    # Model X: the agent already lives in the container, so its roots are real.
    assert agent_types._sandbox_of(_Ctx({"sandbox": _HostSandbox()})) is None
    assert agent_types._sandbox_of(_Ctx({})) is None
    assert agent_types._sandbox_of(_Ctx(None)) is None


def test_unreachable_root_marker_names_no_path():
    # The marker must not look like a path, or the agent will just try it.
    assert "/" not in agent_types._UNREACHABLE_ROOT
    assert "sandbox" in agent_types._UNREACHABLE_ROOT


# --- ambient context inheritance across delegation --------------------------
# Regression test for the same ProgramBench failure's root cause:
# protocol_manager.delegate() built the sub-agent a fresh AgentContext carrying
# only lineage + allowlists, so `sandbox` never crossed the boundary. bash_tool
# and Agent._resolve_workspace_root both read it off the context, so the child
# ran its shell commands on the HOST while its parent ran in the container.

from agentevolver.protocol.server import _AMBIENT_CONTEXT_KEYS, _inherited_ambient  # noqa: E402


def test_inherited_ambient_carries_the_execution_environment():
    parent = _Ctx({
        "sandbox": _PeerSandbox(),
        "project_root": "/host/proj",
        "workspace_root": "/host/proj/workspace",
        "log_root": "/host/proj/log",
        "extension_root": "/host/ext",
        "package_root": "/pkg",
        "shared_extension_root": "/shared",
    })
    got = _inherited_ambient(parent)
    assert sorted(got) == sorted(_AMBIENT_CONTEXT_KEYS)
    assert got["sandbox"] is parent.extra["sandbox"]


def test_inherited_ambient_drops_per_delegation_keys():
    parent = _Ctx({
        "sandbox": _PeerSandbox(),
        "tool_allowlist": ["bash_tool"],
        "skill_allowlist": [],
        "target_name": "some_tool",
        "subtask_id": "abc",
        "parent_session_id": "xyz",
    })
    got = _inherited_ambient(parent)
    assert got == {"sandbox": parent.extra["sandbox"]}


def test_inherited_ambient_tolerates_a_contextless_parent():
    assert _inherited_ambient(None) == {}
    assert _inherited_ambient(_Ctx(None)) == {}
