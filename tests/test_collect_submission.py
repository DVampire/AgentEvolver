"""`collect_submission` packages the agent's reconstruction into the tarball the grader
scores. It is the seam where real work becomes a score, and it is exactly where a real
ProgramBench run lost its score: the `zoxide` reconstruction compiled and had a
`compile.sh`, but the packaging step shipped a submission the grader marked zero. So the
behaviours this file pins are not hypothetical — each corresponds to a way a scorable
reconstruction was, or could be, turned into a zero by the packaging alone.

The rules, from the function's own contract:
  - commit your solution, .gitignore your scratch files, and the committed tree is what
    ships (`git archive HEAD`) — so an agent that keeps its workspace clean ships clean.
  - BUT a committed tree without `compile.sh` scores zero by definition, so fall back to
    shipping the whole directory, which can only help — never turn a scoring mistake into
    a guaranteed zero by being strict.
  - the stashed reference binary and the plan file (`_SCAFFOLDING`) never ship, whichever
    path is taken — the binary is the answer key.
  - a file that cannot be read (the reference binary is root-owned, mode `---x--x--x`) is
    skipped, not fatal: one unreadable file must not turn a weak submission into no
    submission at all.
"""

from __future__ import annotations

import os
import subprocess
import tarfile

import pytest

from agentevolver.benchmark.default.programbench import ProgramBenchmark
rpb = ProgramBenchmark()

collect_submission = rpb._package_submission
REFERENCE_COPY = rpb.reference_copy  # "reference_executable"


def _git(workspace, *args):
    subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True, text=True)


def _repo(workspace):
    """A workspace as the harness seeds it: a git repo whose first commit is the shipped
    documentation (so `commit_count == 1` before the agent commits anything), with the
    scaffolding files gitignored the way `seed_workspace` arranges."""
    workspace.mkdir(parents=True, exist_ok=True)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "t@t")
    _git(workspace, "config", "user.name", "t")
    (workspace / ".gitignore").write_text(f"{REFERENCE_COPY}\nplan.md\n")
    (workspace / "README.md").write_text("task documentation shipped with the image\n")
    _git(workspace, "add", ".gitignore", "README.md")
    _git(workspace, "commit", "-qm", "initial documentation")


def _names(dest_dir):
    with tarfile.open(os.path.join(dest_dir, "submission.tar.gz")) as handle:
        return {n.lstrip("./") for n in handle.getnames() if n not in (".", "./")}


def test_a_committed_tree_with_compile_sh_ships_by_git_archive(tmp_path):
    """The clean case: the agent committed its solution including compile.sh."""
    ws = tmp_path / "ws"
    _repo(ws)
    (ws / "compile.sh").write_text("#!/bin/sh\ncargo build --release\n")
    (ws / "main.rs").write_text("fn main() {}\n")
    _git(ws, "add", "compile.sh", "main.rs")
    _git(ws, "commit", "-qm", "reconstruction")

    info = collect_submission(str(ws), str(tmp_path / "out"))

    assert info["source"] == "git-archive"
    names = _names(tmp_path / "out")
    assert "compile.sh" in names and "main.rs" in names


def test_a_committed_tree_without_compile_sh_falls_back_to_the_whole_tree(tmp_path):
    """The zoxide failure mode: the agent committed source but the build script only
    exists on disk (forgot to `git add` it). Being strict here would ship a guaranteed
    zero; the whole-tree fallback picks compile.sh up from disk so the work can score."""
    ws = tmp_path / "ws"
    _repo(ws)
    (ws / "main.rs").write_text("fn main() {}\n")
    _git(ws, "add", "main.rs")
    _git(ws, "commit", "-qm", "source only")
    # compile.sh written but never committed
    (ws / "compile.sh").write_text("#!/bin/sh\ncargo build --release\n")

    info = collect_submission(str(ws), str(tmp_path / "out"))

    assert info["source"] == "full-tree"
    assert "compile.sh" in _names(tmp_path / "out"), (
        "the uncommitted compile.sh must be recovered from disk — otherwise a "
        "reconstruction that only forgot to `git add` one file scores zero"
    )


def test_no_agent_commit_ships_the_whole_tree(tmp_path):
    """Only the initial documentation commit exists; the agent committed nothing. The
    work is all uncommitted on disk, so the whole tree is what can score."""
    ws = tmp_path / "ws"
    _repo(ws)
    (ws / "compile.sh").write_text("#!/bin/sh\ncargo build\n")
    (ws / "main.rs").write_text("fn main() {}\n")

    info = collect_submission(str(ws), str(tmp_path / "out"))

    assert info["source"] == "full-tree"
    assert {"compile.sh", "main.rs"} <= _names(tmp_path / "out")


def test_the_reference_binary_and_plan_never_ship(tmp_path):
    """The reference binary is the answer key; it must not appear in either path."""
    ws = tmp_path / "ws"
    _repo(ws)
    (ws / "main.rs").write_text("fn main() {}\n")
    (ws / "compile.sh").write_text("#!/bin/sh\ncargo build\n")
    # The agent left the scaffolding in the workspace; .gitignore keeps it uncommitted.
    (ws / REFERENCE_COPY).write_text("<the answer-key binary>")
    (ws / "plan.md").write_text("my plan")

    # full-tree path (nothing committed but the docs) is the one that walks the directory
    info = collect_submission(str(ws), str(tmp_path / "out"))
    assert info["source"] == "full-tree"
    names = _names(tmp_path / "out")
    assert REFERENCE_COPY not in names
    assert "plan.md" not in names
    assert "compile.sh" in names


def test_an_unreadable_file_is_skipped_not_fatal(tmp_path):
    """The reference binary ships mode `---x--x--x` (executable, unreadable). If the agent
    never renamed it to `reference_executable`, `_SCAFFOLDING` does not cover it and the
    directory walk hits EACCES on it. That must skip the one file, not abort the whole
    submission — an aborted submission scores zero for a reason unrelated to the work."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions, so EACCES cannot be provoked")
    ws = tmp_path / "ws"
    _repo(ws)
    (ws / "compile.sh").write_text("#!/bin/sh\ncargo build\n")
    (ws / "main.rs").write_text("fn main() {}\n")
    unreadable = ws / "executable"  # the un-renamed reference binary
    unreadable.write_text("binary")
    os.chmod(unreadable, 0o111)  # --x--x--x: executable, not readable

    try:
        info = collect_submission(str(ws), str(tmp_path / "out"))
    finally:
        os.chmod(unreadable, 0o644)  # so tmp_path cleanup can remove it

    assert info["source"] == "full-tree"
    names = _names(tmp_path / "out")
    assert "compile.sh" in names and "main.rs" in names
    assert "executable" not in names
    assert "executable" in " ".join(info.get("skipped_unreadable", []))
