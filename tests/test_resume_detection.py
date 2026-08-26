"""`--resume` continues from a session's existing workspace instead of re-seeding it, so a
run can build on the reconstruction a prior run already produced rather than start over.
Whether a workspace is worth resuming from is decided by `has_resumable_work`: it needs a
committed solution beyond the shipped initial commit (so `git archive HEAD` yields source),
a build script, and the preserved reference binary that is the only oracle. Missing any of
those, a resume would be worse than a clean re-seed, so it declines and the caller seeds
fresh. These tests pin that gate.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess

_SPEC = importlib.util.spec_from_file_location(
    "rpb_for_resume_tests",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "run_programbench.py"),
)
rpb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rpb)

has_resumable_work = rpb.has_resumable_work
REFERENCE_COPY = rpb.REFERENCE_COPY  # "reference_executable"


def _git(ws, *args):
    subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True, text=True)


def _workspace(tmp_path, *, commits, compile_sh, reference):
    ws = tmp_path / "ws"
    ws.mkdir()
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("shipped docs\n")
    _git(ws, "add", "README.md")
    _git(ws, "commit", "-qm", "Initial commit")
    if commits > 1:
        for i in range(commits - 1):
            (ws / f"src{i}.rs").write_text(f"fn main() {{}} // {i}\n")
            _git(ws, "add", f"src{i}.rs")
            _git(ws, "commit", "-qm", f"reconstruction {i}")
    if compile_sh:
        (ws / "compile.sh").write_text("#!/bin/sh\ncargo build\n")
    if reference:
        (ws / REFERENCE_COPY).write_text("<oracle bytes>")
    return ws


def test_a_full_prior_workspace_is_resumable(tmp_path):
    ws = _workspace(tmp_path, commits=3, compile_sh=True, reference=True)
    assert has_resumable_work(str(ws))


def test_only_the_initial_commit_is_not_resumable(tmp_path):
    # A workspace with just the shipped docs commit has no reconstruction to continue.
    ws = _workspace(tmp_path, commits=1, compile_sh=True, reference=True)
    assert not has_resumable_work(str(ws))


def test_a_missing_reference_binary_declines_resume(tmp_path):
    # Without the oracle on disk there is nothing to reconstruct against — resume would be
    # worse than a clean re-seed, so it must decline.
    ws = _workspace(tmp_path, commits=3, compile_sh=True, reference=False)
    assert not has_resumable_work(str(ws))


def test_a_missing_compile_script_declines_resume(tmp_path):
    ws = _workspace(tmp_path, commits=3, compile_sh=False, reference=True)
    assert not has_resumable_work(str(ws))


def test_a_nonexistent_workspace_is_not_resumable(tmp_path):
    assert not has_resumable_work(str(tmp_path / "does-not-exist"))
