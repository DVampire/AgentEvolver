import argparse
import os
import sys
from pathlib import Path

import pytest
from types import SimpleNamespace

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


def _workspace_with_a_commit(tmp_path, *, commit_source=True, gitignore=True):
    """A workspace shaped like a task container's: the repository arrives with one
    commit holding the shipped docs, and the agent's work goes on top."""
    import subprocess

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "README.md").write_text("docs\n")
    (ws / rp.REFERENCE_COPY).write_bytes(b"\x7fELF-reference")

    def git(*args):
        subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("add", "README.md")
    git("commit", "-q", "-m", "Initial commit")

    (ws / "prog.c").write_text("int main(){}\n")
    (ws / "compile.sh").write_text("#!/bin/sh\ngcc -o executable prog.c\n")
    (ws / "scratch.out").write_text("comparison dump\n")
    if gitignore:
        (ws / ".gitignore").write_text("scratch.out\nexecutable\n")
    if commit_source:
        git("add", "prog.c", "compile.sh", *( [".gitignore"] if gitignore else [] ))
        git("commit", "-q", "-m", "reconstruct")
    return ws


def test_committed_tree_is_what_ships(tmp_path):
    """This is what makes the task document's "commit your solution" and
    ".gitignore your build artifacts" mean anything. Left to a plain tar, neither
    instruction affected the deliverable: one run shipped 70 files of which 6 were the
    solution, the rest comparison dumps."""
    ws = _workspace_with_a_commit(tmp_path)
    info = rp.collect_submission(str(ws), str(tmp_path / "out"))

    assert info["source"] == "git-archive"
    shipped = {p.name for p in (tmp_path / "out" / "submission").iterdir()}
    assert {"prog.c", "compile.sh", "README.md"} <= shipped
    # The gitignored scratch file is gone, and so is the reference binary.
    assert "scratch.out" not in shipped
    assert rp.REFERENCE_COPY not in shipped


def test_a_workspace_with_no_commit_still_ships_everything(tmp_path):
    """An agent that forgot to commit must not be handed a guaranteed zero."""
    ws = _workspace_with_a_commit(tmp_path, commit_source=False)
    info = rp.collect_submission(str(ws), str(tmp_path / "out"))

    assert info["source"] == "full-tree"
    shipped = {p.name for p in (tmp_path / "out" / "submission").iterdir()}
    assert {"prog.c", "compile.sh"} <= shipped
    assert rp.REFERENCE_COPY not in shipped
    assert "no commit beyond" in (info["note"] or "")


def test_a_committed_tree_without_compile_sh_falls_back_to_the_whole_tree(tmp_path):
    """A submission without compile.sh scores zero by definition, so when the committed
    tree lacks it the fuller tree can only help. Being strict here would turn a scoring
    mistake into a guaranteed zero."""
    import subprocess

    ws = _workspace_with_a_commit(tmp_path, commit_source=False)
    subprocess.run(["git", "-C", str(ws), "add", "prog.c"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", "partial"], check=True, capture_output=True)

    info = rp.collect_submission(str(ws), str(tmp_path / "out"))
    assert info["source"] == "full-tree"
    assert "compile.sh" in (info["note"] or "")
    shipped = {p.name for p in (tmp_path / "out" / "submission").iterdir()}
    assert "compile.sh" in shipped


def test_uncommitted_work_is_recorded_not_silently_dropped(tmp_path):
    """The most useful thing to know when a submission turns out to be missing
    something."""
    ws = _workspace_with_a_commit(tmp_path, gitignore=False)
    info = rp.collect_submission(str(ws), str(tmp_path / "out"))
    assert "scratch.out" in info["uncommitted"]
    # The stashed reference is scaffolding, not forgotten work.
    assert rp.REFERENCE_COPY not in info["uncommitted"]


def test_the_reference_binary_audit_flags_only_the_reference(tmp_path):
    """The run is root, which bypasses the permission bits the official image uses to
    make the reference unreadable — so the prohibition holds by instruction, and an
    instruction is worth checking. Analysis of the agent's own build is allowed."""
    trace = tmp_path / "trace"
    trace.mkdir()
    (trace / "a.jsonl").write_text(
        '{"action_name": "bash_tool", "input": {"command": "objdump -d ./executable"}}\n'
        '{"action_name": "bash_tool", "input": {"command": "strings my_build"}}\n'
        '{"action_name": "bash_tool", "input": {"command": "timeout 2 ./executable --help"}}\n'
    )
    audit = rp.audit_reference_binary(str(tmp_path))
    assert audit["checked"] is True
    tools = [hit["tool"] for hit in audit["suspicious_actions"]]
    assert tools == ["objdump"]


class _Ctx:
    def __init__(self, extra):
        self.extra = extra


# --- ambient context inheritance across delegation --------------------------
# A sub-agent runs where its parent runs, so the roots describing that place have to
# cross the delegation boundary. They once did not: delegate() built the child a fresh
# context carrying only lineage and allowlists, and the child then hunted for files
# where they were not — one run spent a full tool timeout on `find /`.

from agentevolver.protocol.server import _AMBIENT_CONTEXT_KEYS, _inherited_ambient  # noqa: E402


def test_inherited_ambient_carries_the_execution_environment():
    parent = _Ctx({
        "project_root": "/proj",
        "workspace_root": "/proj/workspace",
        "log_root": "/proj/log",
        "extension_root": "/ext",
        "package_root": "/pkg",
        "shared_extension_root": "/shared",
    })
    got = _inherited_ambient(parent)
    assert sorted(got) == sorted(_AMBIENT_CONTEXT_KEYS)
    assert got["workspace_root"] == "/proj/workspace"


def test_inherited_ambient_drops_per_delegation_keys():
    parent = _Ctx({
        "project_root": "/proj",
        "tool_allowlist": ["bash_tool"],
        "skill_allowlist": [],
        "target_name": "some_tool",
        "subtask_id": "abc",
        "parent_session_id": "xyz",
    })
    got = _inherited_ambient(parent)
    # Only the "where execution happens" keys cross; the target, the allowlists and the
    # lineage ids are per-delegation and would misdescribe the child if inherited.
    assert got == {"project_root": "/proj"}


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


def test_docstring_explains_where_the_agent_runs_and_why():
    """Both halves matter and neither is obvious. The agent runs in the task's own image
    because that is the only place its toolchain exists; the container has no network
    because that is the benchmark's anti-cheat."""
    doc = rp.__doc__
    assert "inside the task's own image" in doc
    assert "no network interface" in doc
    assert "anti-cheat" in doc


def test_the_inner_command_points_at_the_mounted_checkout():
    """Host paths do not exist inside the container; the same checkout is mounted
    elsewhere, so the paths handed to the inner run have to be rewritten."""
    instance = {"instance_id": "org__proj.abc1234", "repository": "org/proj", "language": "c"}
    args = SimpleNamespace(
        config=f"{rp.root}/configs/programbench_agent_baseline.py",
        task_file=f"{rp.root}/examples/tasks/programbench_reconstruction.html",
    )
    command = rp.inner_command(instance, args)
    assert f"{rp.CONTAINER_REPO}/examples/run_programbench.py" in command
    assert f"{rp.CONTAINER_REPO}/configs/programbench_agent_baseline.py" in command
    assert rp.root not in command
    # The metadata travels on the command line because the dataset is not importable in
    # there and nothing can be installed without network.
    assert "org__proj.abc1234" in command


def test_the_interpreter_is_mounted_at_its_own_path():
    """A conda or virtual environment records absolute paths, so attaching it anywhere
    other than where it was created breaks it."""
    prefix = rp.interpreter_prefix()
    assert os.path.isabs(prefix)
    assert prefix in sys.executable


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
    """The agent cannot see the tests, so it can never *know* it is finished. Without an
    explicit rule a run keeps refining until something else stops it, and the last thing
    it needed to do — verify the build, commit — is what gets cut."""
    prompt = _task_text()
    assert "## when-to-stop" in prompt
    flat = _flat_task_text()
    # Absolute, not proportional. A fraction of the budget means a different amount of
    # work depending on how large the budget is, while the part it protects — one clean
    # build and a commit — costs the same either way.
    assert "Fewer than 40 steps, or 20 minutes, of your budget remain" in flat
    assert "Reserve an amount, not a fraction." in flat
    assert "two thirds" not in flat
    # And the primary condition stays the honest one: coverage, not exhaustion.
    assert "Every checklist item is covered" in flat



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


# --- where the run works, and where its own output goes -------------------------
#
# Two mirror-image mistakes, both fatal and neither loud: the agent working in the
# session directory instead of the task's, or the framework's output tree landing inside
# the task workspace and shipping in the submission.

class _FsSandbox:
    def __init__(self, project_root):
        self.project_root = project_root
        self.workspace_root = os.path.join(project_root, "workspace")


def test_the_task_workspace_is_the_working_directory():
    """Without this the agent cannot read the documentation that *is* the specification:
    check_session_path permits reads only under the session roots, so
    read_file_tool('/workspace/README.md') answers "Sandbox denied read outside allowed
    roots"."""
    from agentevolver.sandbox.project import check_session_path

    ctx = SimpleNamespace(extra={"project_root": "/AgentEvolver/output/local/sessions/x"})
    rp.bind_task_workspace(ctx, _FsSandbox("/AgentEvolver/output/local/sessions/x"))

    assert config.workspace_root == rp.CONTAINER_WORKSPACE
    assert ctx.extra["workspace_root"] == rp.CONTAINER_WORKSPACE
    # The decisive consequence: the task's own files are reachable.
    assert check_session_path(ctx, "/workspace/README.md", write=False) is None
    assert check_session_path(ctx, "/workspace/prog.c", write=True) is None


def test_a_session_tree_inside_the_task_workspace_is_refused():
    """It would ship in the submission — a directory of logs is not a reconstruction.
    Depends on the process's working directory, so it is checked, not assumed."""
    ctx = SimpleNamespace(extra={})
    with pytest.raises(RuntimeError, match="inside the task workspace"):
        rp.bind_task_workspace(ctx, _FsSandbox("/workspace/output/local/sessions/x"))


def test_the_stashed_reference_stays_out_of_the_submission(tmp_path):
    """Shipping it would hand the grader the original binary — and a compile.sh that
    copied it into place would score as a real reconstruction."""
    ws = _workspace_with_a_commit(tmp_path, commit_source=False)

    info = rp.collect_submission(str(ws), str(tmp_path / "out"))

    shipped = {p.name for p in (tmp_path / "out" / "submission").iterdir()}
    assert rp.REFERENCE_COPY not in shipped
    assert "compile.sh" in shipped
    # Nor is it reported as work the agent forgot to commit: it is scaffolding.
    assert rp.REFERENCE_COPY not in info["uncommitted"]


def test_the_audit_does_not_flag_the_prompt_that_forbids_the_tools(tmp_path):
    """The task rules name `objdump` in the course of forbidding it, and the prompt
    travels through the trace — so scanning raw lines accused every run of exactly the
    thing being checked for. Only what the agent ran counts."""
    trace = tmp_path / "trace"
    trace.mkdir()
    (trace / "a.jsonl").write_text(
        # the task document quoted inside a trace event
        '{"action_name": null, "event_type": "agent_start", "input": '
        '{"task": "Run binary-analysis tools (objdump, readelf) on ./executable is forbidden"}}\n'
        # the agent running an allowed command against the reference
        '{"action_name": "bash_tool", "input": {"command": "timeout 2 ./executable --help"}}\n'
        # analysis of its own build, which is allowed
        '{"action_name": "bash_tool", "input": {"command": "objdump -d my_build"}}\n'
    )
    assert rp.audit_reference_binary(str(tmp_path))["suspicious_actions"] == []


def test_the_audit_does_flag_analysis_of_the_reference(tmp_path):
    trace = tmp_path / "trace"
    trace.mkdir()
    (trace / "a.jsonl").write_text(
        '{"action_name": "bash_tool", "input": {"command": "objdump -d ./executable | head"}}\n'
    )
    hits = rp.audit_reference_binary(str(tmp_path))["suspicious_actions"]
    assert [h["tool"] for h in hits] == ["objdump"]
    assert hits[0]["action"] == "bash_tool"


def test_the_submission_is_written_beside_the_workspace_not_into_it():
    """The session's `workspace/` is the directory mounted at /workspace, so writing the
    tarball into it would archive the archive and leave the unpacked copy inside the
    deliverable."""
    import inspect

    source = inspect.getsource(rp.run_inner)
    assert "collect_submission(CONTAINER_WORKSPACE, str(fs_sandbox.project_root))" in source
    assert "fs_sandbox.workspace_root" not in source


def test_the_workspace_is_seeded_from_the_image_before_being_mounted():
    """Mounting a directory straight onto /workspace would hide what the image ships
    there — the documentation that *is* the specification, the reference binary, the git
    repository — and leave the agent an empty room."""
    import inspect

    source = inspect.getsource(rp.seed_workspace)
    # cp -a from inside the image, so the reference binary's ---x--x--x survives: a copy
    # that widened it would hand over the bytes the benchmark withholds.
    assert "cp -a /workspace/. /seed/" in source
    assert "docker" in source and "--rm" in source
    # A previous run's root-owned files are cleared first; mixing them in would make last
    # run's source look like this run's work.
    assert "rm -rf /seed/" in source

    launcher = inspect.getsource(rp.run_launcher)
    assert "seed_workspace(image_ref, workspace_dir)" in launcher
    assert "workspace_dir: CONTAINER_WORKSPACE" in launcher


def test_every_path_comes_from_the_layout_table():
    """No path assembly here. The launcher seeds the directory the inner run then works
    in, so both have to name the same one — asking the layout is how they cannot drift,
    and passing the owner explicitly is how it stops being an accident that two defaults
    in different modules happen to match."""
    import inspect

    launcher = inspect.getsource(rp.run_launcher)
    assert "path_manager.get(" in launcher
    assert "P.SESSION_WORKSPACE" in launcher
    assert "P.SESSION," in launcher
    assert not hasattr(rp, "session_dir"), "the bespoke path helper is back"

    inner = inspect.getsource(rp.run_inner)
    assert "owner=SESSION_OWNER" in inner


def test_the_task_says_nothing_is_a_prerequisite():
    """A run decided the reference's crash on one flag was "a critical edge case to
    understand before proceeding" and spent a hundred consecutive turns on it without
    writing a line of source. The instruction to skip unreachable differences was already
    there; what was missing was any basis for deciding one *is* unreachable, and the fact
    that the items do not block each other."""
    prompt = _flat_task_text()
    assert "Nothing here is a prerequisite for anything else." in prompt
    assert '"I need to understand this before proceeding" is, for this task, almost always false' in prompt
    # The three signatures, each checkable without knowing the tests.
    assert "The reference crashes, hangs, or depends on something you do not have." in prompt
    assert "a value only the original build could know" in prompt
    assert "roughly ten turns on one difference without your source changing" in prompt
