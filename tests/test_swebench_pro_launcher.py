"""Focused tests for host-side SWE-bench Pro patch collection."""

from __future__ import annotations

import subprocess

from examples.run_swebench_pro import collect_patch


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_collect_patch_is_read_only_and_includes_untracked_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repo / "new.txt").write_text("new\n", encoding="utf-8")
    index_before = (repo / ".git" / "index").read_bytes()

    patch = collect_patch(str(repo), base)

    assert "diff --git a/tracked.txt b/tracked.txt" in patch
    assert "diff --git a/new.txt b/new.txt" in patch
    assert (repo / ".git" / "index").read_bytes() == index_before
    assert _git(repo, "status", "--short").splitlines() == ["M tracked.txt", "?? new.txt"]
