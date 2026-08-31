"""Focused tests for host-side SWE-bench Pro patch collection."""

from __future__ import annotations

import asyncio
import subprocess
import threading

import pytest

import examples.run_swebench_pro as swebench_pro
from examples.run_swebench_pro import _as_list, collect_patch, parse_cfg_options


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
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


def test_as_list_accepts_the_dataset_python_literal_fallback():
    # A quote inside a test name is escaped for Python, but not sufficiently for JSON.
    row = {"fail_to_pass": "['works with \\'quoted\\' input', 'second test']"}

    assert _as_list(row, "fail_to_pass") == ["works with 'quoted' input", "second test"]


def test_cfg_options_are_a_mapping_before_config_initialization():
    assert parse_cfg_options(
        [
            "model_name=llm_hub/deepseek-v4-flash",
            "output_owner=swebench_pro_deepseek_v4_flash",
        ]
    ) == {
        "model_name": "llm_hub/deepseek-v4-flash",
        "output_owner": "swebench_pro_deepseek_v4_flash",
    }


@pytest.mark.asyncio
async def test_workspace_seeding_does_not_block_the_event_loop(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_seed(*_args):
        started.set()
        assert release.wait(timeout=2)

    monkeypatch.setattr(swebench_pro, "seed_workspace", blocking_seed)
    task = asyncio.create_task(swebench_pro.seed_workspace_async("image", "commit", "/workspace"))
    assert await asyncio.to_thread(started.wait, 1)

    # This coroutine can still run while the synchronous Docker copy is in its thread.
    await asyncio.sleep(0)
    assert not task.done()

    release.set()
    await task
