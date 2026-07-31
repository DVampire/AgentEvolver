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


#: The instructions now live in examples/tasks/programbench_reconstruction.html, so
#: these tests assert on the text the agent is actually handed — placeholders filled,
#: sections labelled — rather than on a module constant that no longer exists.
_SAMPLE_INSTANCE = {
    "repository": "abishekvashok/cmatrix",
    "language": "c",
    "image_name": "programbench/abishekvashok_1776_cmatrix.5c082c6",
    "commit": "5c082c6",
    "instance_id": "abishekvashok__cmatrix.5c082c6",
}


def _task_text(instance=None):
    content, _files, _meta = rp.build_task_content(instance or _SAMPLE_INSTANCE)
    return content


def _flat_task_text(instance=None):
    """Whitespace-normalised: where prose happens to wrap is not part of the contract."""
    return " ".join(_task_text(instance).split())


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


def test_build_task_content_fills_the_target_into_the_document():
    content, files, meta = rp.build_task_content(_SAMPLE_INSTANCE)
    # Only the target varies across the 201 instances, and it belongs in the
    # objective — the agent should know what it is rebuilding before reading the
    # method.
    assert "abishekvashok/cmatrix" in content
    assert "language: c" in content
    assert "{repository}" not in content and "{language}" not in content
    assert "./executable" in content
    assert "./compile.sh" in content
    # Sections survive the HTML->text conversion as labels.
    for section in ("## objective", "## rules", "## when-to-stop", "## deliverable"):
        assert section in content, section
    # The document itself is attached, as resolve_task would.
    assert files and files[0].endswith("programbench_reconstruction.html")
    assert meta["task_doc"] == files[0]


def test_build_task_content_renders_a_substituted_view(tmp_path):
    """Filling only the agent's text leaves a human opening the view on a raw
    `{repository}`."""
    content, _files, meta = rp.build_task_content(_SAMPLE_INSTANCE, None, str(tmp_path))
    view = meta["task_view"]
    assert os.path.isfile(view)
    html = open(view, encoding="utf-8").read()
    assert "abishekvashok/cmatrix" in html
    assert "{repository}" not in html


def test_a_document_without_the_placeholders_is_flagged(tmp_path, caplog):
    """A swapped-in prompt that forgets the slots must not silently lose the target."""
    doc = tmp_path / "no_slots.html"
    doc.write_text("<html><body><div class='task'><objective>Rebuild it.</objective>"
                   "</div></body></html>", encoding="utf-8")
    with caplog.at_level("WARNING"):
        content, _files, _meta = rp.build_task_content(_SAMPLE_INSTANCE, str(doc))
    assert "Rebuild it." in content


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

def test_task_tells_the_agent_to_preserve_the_reference():
    prompt = _task_text()
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

def test_task_requires_timeout_around_either_binary():
    prompt = _task_text()
    assert "timeout" in prompt
    # Both binaries, since either can hang once the reconstruction is wrong.
    assert "timeout 2 ./reference_executable" in prompt
    # Only one worked example now; the rule covers "either binary" in prose, because
    # the shapes vary too much (TUI, server, compiler, filter) for a per-shape example.
    assert "either binary" in prompt.lower()
    # The exit code that identifies a hang, so the agent can act on it.
    assert "124" in prompt
    # And why it matters inside a batch — one hang stalls the whole turn.
    assert "batch" in prompt.lower()


def test_bash_tool_timeout_is_left_alone_for_slow_builds():
    """Guards the decision above: don't 'fix' the hang by capping every build."""
    from agentevolver.tool.default.bash import BashTool

    assert BashTool().timeout >= 600


# --- the prompt must generalise across all 201 instances ---------------------
# The dataset is not 201 cmatrix clones: 107 Rust / 46 Go / 33 C / 12 C++, and the
# shapes run from single-flag CLIs to FFmpeg, sqlite, duckdb, tinycc, miniserve and
# tree-sitter. Test counts span 3 to 20530. An earlier prompt told the agent to work
# "flag by flag" and to reproduce `--help`/`--version` byte-for-byte — true for
# cmatrix, where help is 115 of 506 tests, and wrong for a library or a compiler.

def test_prompt_covers_the_program_shapes_in_the_dataset():
    prompt = _task_text().lower()
    for shape in ("command-line tool", "filter", "compiler", "tui", "server", "library"):
        assert shape in prompt, shape
    # And says what NOT to chase for the shapes where exhaustive fidelity is hopeless.
    assert "frame-by-frame" in prompt
    assert "live protocol traffic" in prompt



def test_task_makes_the_documentation_the_specification():
    """The benchmark ships no problem statement — the docs are it. An agent that
    skims them cannot reproduce behaviour it never learned exists."""
    prompt = _flat_task_text()
    assert "There is no separate problem statement." in prompt
    assert "is* the specification you are graded against" in prompt
    # Filenames differ per instance (cmatrix.1/COPYING/data vs
    # how_to_do_things_safely_in_bash.md/LICENSE/img), so the document must send the
    # agent to enumerate rather than name files.
    assert "filenames vary by project, enumerate rather than guess" in prompt
    assert "find . -path ./.git -prune -o -type f -print" in prompt
    assert "read **every** text file end to end" in prompt


def test_task_describes_the_verified_container_layout():
    """These four facts were checked against the real task images; getting any of
    them wrong costs the agent turns."""
    prompt = _flat_task_text()
    # The repo is NOT empty: one Initial commit holds the shipped docs, and its
    # --stat is the original file list.
    assert "one `Initial commit` containing exactly the documentation files" in prompt
    assert "git show --stat --oneline HEAD" in prompt
    assert "empty git repository" not in prompt
    # The reference is execute-only by design.
    assert "mode `---x--x--x`" in prompt
    assert "There is no internet." in prompt


def test_task_says_man_page_renderers_are_unavailable():
    """`man` in the task image is a stub from a minimized system, and nroff/groff/col
    are absent — the man page has to be read as raw roff."""
    prompt = _flat_task_text()
    assert "`man`, `nroff` and `groff` are unavailable here" in prompt
    assert "do not spend turns on them" in prompt


def test_task_gathers_the_brief_before_mapping_the_surface():
    """Deciding what kind of program this is presumes having read the docs."""
    prompt = _task_text()
    assert prompt.index("## gather-the-brief") < prompt.index("## map-the-surface")
    # ...but copying the reference aside still comes first: the first build
    # overwrites it.
    assert prompt.index("## preserve-the-reference") < prompt.index("## gather-the-brief")


def test_task_does_not_treat_a_readable_reference_as_permission():
    """Our peer sandbox runs as root, so `objdump ./executable` succeeds where the
    official harness (user `agent`) returns Permission denied. The rule has to hold
    regardless of that quirk."""
    prompt = _flat_task_text()
    assert "if a permissions quirk of your sandbox lets one of these read it anyway, that is not permission to use it" in prompt


def test_task_states_the_verified_grading_contract():
    """Read off programbench/eval/eval.py: the grader wipes /workspace, extracts only
    the submission, deletes any shipped ./executable, and runs compile.sh with DNS
    blocked under a 900s timeout. An agent that does not know this writes a compile.sh
    that fetches dependencies and scores zero."""
    prompt = _flat_task_text()
    assert "with no network and a 15-minute limit" in prompt
    assert "must build from your submitted files alone" in prompt
    assert "at grading time they are not" in prompt
    assert "Shipping a prebuilt binary is pointless" in prompt
    assert "detected by hash and removed" in prompt


def test_task_says_the_agent_cannot_run_the_graded_tests():
    """The suites live in a separate HF dataset and are streamed into a fresh container
    after the run — there is nothing to self-check against but the reference."""
    prompt = _flat_task_text()
    assert "You never see the tests and cannot run them" in prompt
    assert "reference binary is your only oracle" in prompt


def test_task_points_at_the_surface_that_actually_scores():
    """313k graded cases across the 201 tests.json manifests are dominated by CLI
    argument handling: flag, help, invalid, error, empty, format are the top keywords,
    and test_help_usage/test_errors/test_argparse_validation the top modules."""
    prompt = _flat_task_text()
    assert "score is a fraction of many individual test cases, not pass/fail" in prompt
    assert "both long and short form" in prompt
    assert "exact `--help`/usage text" in prompt
    assert "the message *and* the exit status" in prompt
    assert "several flags used together" in prompt

def test_task_states_a_stopping_rule():
    """Without one the agent never finishes: the grading tests are hidden from it, so
    there is always one more difference to find. A run reached 227 turns and 48
    minutes still comparing flags, having never been told what 'done' means."""
    prompt = _task_text()
    assert "## when-to-stop" in prompt
    # A budget-based rule, not just "when it is perfect".
    assert "two thirds" in prompt
    assert "done_tool" in prompt
    # And explicit permission to abandon what cannot be matched.
    assert "unreachable" in prompt
    assert "timestamp" in prompt


def test_prompt_puts_the_build_before_refinement():
    """A submission that does not compile scores zero regardless of source quality."""
    # Whitespace-normalised: where prose happens to wrap is not part of the contract.
    prompt = _flat_task_text()
    assert "scores zero" in prompt
    assert "clean checkout of your source alone" in prompt
    assert "Non-negotiable" in prompt


def test_prompt_orders_breadth_before_depth():
    prompt = _task_text()
    assert "## go-wide-before-deep" in prompt
    assert "checklist" in prompt
