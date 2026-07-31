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
    # This arm carries the self-evolution roster outright; the control arm is a
    # separate config file, not a flag.
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
    # Retrieval would defeat the benchmark's anti-cheat: the agent has twice been
    # observed trying to fetch the original source (`git clone`, `curl`), blocked
    # only by network isolation.
    for banned in ("web_searcher_tool", "web_fetcher_tool", "media_search_tool", "http_request_tool"):
        assert banned not in config.tool_names, banned
    # Still lean — no document, science or unrelated workflow skills.
    assert "docx_skill" not in config.skill_names
    assert "observability_and_instrumentation_skill" not in config.skill_names
    assert config.connector_names == []
    assert config.env_names == []


#: The methodology skills that must appear in *both* experiment arms — they are
#: how an agent checks its own work, not the variable under test.
VERIFICATION_SKILLS = (
    "verify_skill",
    "test_driven_development_skill",
    "debugging_and_error_recovery_skill",
    "incremental_implementation_skill",
    "source_driven_development_skill",
)

EVOLUTION_AGENTS = (
    "tool_generate_agent", "tool_optimize_agent", "tool_evaluate_agent",
    "agent_generate_agent", "agent_optimize_agent", "agent_evaluate_agent",
    "skill_generate_agent", "skill_optimize_agent", "skill_evaluate_agent",
)


def test_baseline_config_carries_no_evolution_capability():
    """What makes the control arm a control arm.

    Deliberately does NOT pin the full tool roster: that list is iterated on. What
    must hold is that nothing in this config makes Agent._evolution_enabled() true,
    because the derived flag is what renders meta_agent's self-evolution rules — a
    stray `evolution_tool` here would hand a no-evolution run ~1100 tokens of
    instructions for capabilities it does not have.
    """
    def load(name):
        config.initialize(
            config_path=os.path.join(root, "configs", name),
            args=argparse.Namespace(), verbose=False,
        )
        return {k: list(getattr(config, k)) for k in
                ("agent_names", "tool_names", "skill_names")}

    baseline = load("programbench_agent_baseline.py")

    for actor in ("meta_agent", "code_agent", "general_agent", "reviewer_agent"):
        assert actor in baseline["agent_names"]
    assert set(baseline["agent_names"]).isdisjoint(EVOLUTION_AGENTS)
    assert "evolution_tool" not in baseline["tool_names"]
    assert "self_evolving_skill" not in baseline["skill_names"]
    assert not any(s.endswith("_creator_skill") for s in baseline["skill_names"])
    assert not any(n.endswith(("_generate_agent", "_optimize_agent", "_evaluate_agent"))
                   for n in baseline["agent_names"])
    # Retrieval stays out of both arms — it would defeat the anti-cheat.
    for banned in ("web_searcher_tool", "web_fetcher_tool", "media_search_tool", "http_request_tool"):
        assert banned not in baseline["tool_names"], banned


def test_evolving_config_does_enable_the_evolution_prompt():
    """The mirror of the above: the evolving arm must trip the derived flag."""
    config.initialize(
        config_path=os.path.join(root, "configs", "programbench_agent.py"),
        args=argparse.Namespace(), verbose=False,
    )
    assert "evolution_tool" in config.tool_names
    assert set(EVOLUTION_AGENTS) <= set(config.agent_names)


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


# --- the prompt follows the roster ------------------------------------------
# The evolution rules used to render unconditionally, so a run without the
# evolution roster still got ~1100 tokens telling it to invoke
# `self_evolving_skill` and roll back with `evolution_tool` — instructions for
# capabilities that were not loaded. The flag is derived from the live roster so
# the two can never disagree.

from agentevolver.prompt.types import parse_prompt_file, _render_template  # noqa: E402

_PROMPT_ROOTS = dict(
    max_actions=5, extension_root="/e", package_root="/p", project_root="/pr",
    workspace_root="/w", log_root="/l", python_executable="/py",
    python_version="3.12", platform="linux", shell="bash", cwd="/w",
)


def _render_meta_agent(evolution_enabled):
    cfg = parse_prompt_file(
        os.path.join(root, "agentevolver", "prompt", "default", "meta_agent.html")
    )
    return _render_template(
        cfg.system_template, {**_PROMPT_ROOTS, "evolution_enabled": evolution_enabled}
    )


def test_meta_agent_prompt_gates_the_evolution_rules():
    on = _render_meta_agent(True)
    off = _render_meta_agent(False)

    assert "<self-evolution-rules>" in on
    assert "<self-evolution-rules>" not in off
    # The capabilities the rules tell the agent to reach for must not be named
    # when they are not loaded — that is the whole point of the gate.
    for absent in ("self_evolving_skill", "evolution_tool", "tool_optimize_agent"):
        assert absent not in off, absent
    # The lean arm still gets dispatch guidance, just without the evolution half.
    assert "re-dispatch with corrective guidance" in off
    # Gating must actually remove bulk, not just the opening tag.
    assert len(off) < len(on) * 0.85


def test_meta_agent_prompt_renders_with_no_leftover_template_markers():
    for flag in (True, False):
        out = _render_meta_agent(flag)
        assert "{%" not in out and "{{" not in out, flag


def test_evolution_markers_match_what_the_configs_declare():
    """The derived flag keys off these names, so a rename must break loudly."""
    from agentevolver.agent import types as agent_types

    assert agent_types._EVOLUTION_TOOL == "evolution_tool"
    assert agent_types._EVOLUTION_SKILL == "self_evolving_skill"
    for name in EVOLUTION_AGENTS:
        assert name.endswith(agent_types._EVOLUTION_AGENT_SUFFIXES), name


def test_parse_args_requires_a_selector():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py"]
    try:
        with pytest.raises(SystemExit):
            rp.parse_args()
    finally:
        sys.argv = old_argv


def test_parse_args_has_no_evolve_flag():
    """The arm is chosen by --config, so a stale --no-evolve must fail loudly
    rather than be silently ignored and produce an evolving run."""
    old_argv = sys.argv
    sys.argv = ["run_programbench.py", "--start", "0", "--end", "1", "--no-evolve"]
    try:
        with pytest.raises(SystemExit):
            rp.parse_args()
    finally:
        sys.argv = old_argv


def test_parse_args_config_defaults_to_the_evolving_arm():
    old_argv = sys.argv
    sys.argv = ["run_programbench.py", "--start", "0", "--end", "1"]
    try:
        args = rp.parse_args()
    finally:
        sys.argv = old_argv
    assert args.config.endswith("programbench_agent.py")


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
    # Asserting the intent rather than the exact string: what must hold is that the
    # whole workspace is archived and the reference copy is left out of it.
    assert len(sandbox.commands_run) == 1
    tar_cmd = sandbox.commands_run[0]
    assert tar_cmd.startswith("tar -czf /tmp/submission.tar.gz")
    assert "-C /workspace ." in tar_cmd
    assert f"--exclude=./{rp.REFERENCE_COPY}" in tar_cmd
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


# --- the reference binary must survive the agent's first build ---------------
# compile.sh has to write ./executable, which is where the provided reference
# binary sits, so the first build silently destroys the only oracle. Observed on
# cmatrix: gone by command 22 of 65.

def test_system_prompt_tells_the_agent_to_preserve_the_reference():
    prompt = rp.SYSTEM_PROMPT
    assert rp.REFERENCE_COPY in prompt, "the prompt must name the file extract_submission strips"
    assert "cp /workspace/executable" in prompt
    # It must say *why*, or the step reads as optional housekeeping and gets skipped.
    assert "destroys" in prompt or "overwrit" in prompt
    # And differential testing must be spelled out — matching --help is not enough.
    assert "diff" in prompt.lower()


def test_extract_submission_excludes_the_reference_copy():
    """Shipping the reference would hand the grader the original binary, and a
    compile.sh that copied it into place would score as a real reconstruction."""
    import inspect
    src = inspect.getsource(rp.extract_submission)
    assert f"--exclude=./{{REFERENCE_COPY}}" in src or "--exclude" in src
    assert "REFERENCE_COPY" in src


# --- Model X: the MAS belongs inside the base container ----------------------
# A host launch still produces valid submissions, because bash and the file tools
# route into the peer cleanroom either way. That is exactly why it needs saying
# out loud: the divergence is invisible in the results.

def test_warn_if_not_containerized_detects_both_markers(monkeypatch):
    monkeypatch.delenv("AGENTEVOLVER_HOST_ROOT", raising=False)
    monkeypatch.setattr(rp.os.path, "exists", lambda p: False)
    assert rp.warn_if_not_containerized() is False

    monkeypatch.setenv("AGENTEVOLVER_HOST_ROOT", "/mnt/repo")
    assert rp.warn_if_not_containerized() is True

    monkeypatch.delenv("AGENTEVOLVER_HOST_ROOT", raising=False)
    monkeypatch.setattr(rp.os.path, "exists", lambda p: p == "/.dockerenv")
    assert rp.warn_if_not_containerized() is True


def test_docstring_documents_the_sandboxed_launch():
    doc = rp.__doc__
    assert "scripts/run-in-sandbox.sh" in doc
    # And why the two containers exist, so nobody collapses them into one.
    assert "anti-cheat" in doc
    assert "network=False" in doc


# --- the reconstructed program may not exit ----------------------------------
# A run lost 10 minutes to `./executable -z` inside a batched comparison: the
# reference prints usage and exits 0 on an unknown flag, the reconstruction fell
# through into its TUI loop, and bash_tool blocked for its full 600s timeout. The
# hang is itself the defect under test, so it wants finding in two seconds.
#
# Deliberately no matching cap on bash_tool.timeout: 107 of the 201 instances are
# Rust, and a timeout short enough to bound a hang would kill a legitimate
# `cargo build`. The prompt addresses the cause; a tool cap would punish the
# majority case.

def test_system_prompt_requires_timeout_around_either_binary():
    prompt = rp.SYSTEM_PROMPT
    assert "timeout" in prompt
    # Both binaries, since either can hang once the reconstruction is wrong.
    assert "timeout 2 ./reference_executable" in prompt
    assert "timeout 2 ./executable" in prompt
    # The exit code that identifies a hang, so the agent can act on it.
    assert "124" in prompt
    # And why it matters inside a batch — one hang stalls the whole turn.
    assert "batch" in prompt.lower()


def test_bash_tool_timeout_is_left_alone_for_slow_builds():
    """Guards the decision above: don't 'fix' the hang by capping every build."""
    from agentevolver.tool.default.bash import BashTool

    assert BashTool().timeout >= 600
