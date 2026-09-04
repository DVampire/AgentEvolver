"""Focused tests for host-side SWE-bench Pro patch collection."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import types

import pytest

import examples.run_swebench_pro as swebench_pro
from examples.run_swebench_pro import (
    _argv_safe_run_script,
    _as_list,
    _build_entryscript,
    _parser_with_fail_boundaries,
    atomic_write_json,
    collect_patch,
    has_resumable_workspace,
    load_run_results,
    parse_cfg_options,
    scored_instance_ids,
)


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


def test_grader_argv_preserves_comma_inside_one_test_name(tmp_path):
    script = '''if [[ "$1" == *","* ]]; then
    IFS=',' read -r -a TEST_FILES <<< "$1"
else
    TEST_FILES=("$@")
fi'''
    row = {
        "instance_id": "sample",
        "base_commit": "abc123",
        "selected_test_files_to_run": (
            '["Test/schema mismatch,_but skip", "Test/schema mismatch"]'
        ),
    }

    assert 'TEST_FILES=("$@")' in _argv_safe_run_script(script)
    entry = _build_entryscript(row, str(tmp_path))
    assert "'Test/schema mismatch,_but skip'" in entry
    assert "'Test/schema mismatch'" in entry


def test_jest_parser_treats_fail_as_a_file_boundary():
    parser_path = (
        swebench_pro.DEFAULT_GRADER_REPO
        + "/run_scripts/"
        + "instance_element-hq__element-web-72a8f8f03b1a01bb70ef8a5bb61759416991b32c-vnan/parser.py"
    )
    module = types.ModuleType("grader_parser_test")
    sys.modules[module.__name__] = module
    try:
        with open(parser_path, encoding="utf-8") as handle:
            exec(_parser_with_fail_boundaries(handle.read()), module.__dict__)
    finally:
        sys.modules.pop(module.__name__, None)

    parsed = module.parse_test_output(
        "PASS first.test.ts\nSuite A\n  ✓ passes first\n"
        "FAIL second.test.ts\nSuite B\n  ✓ passes second\n  ✕ fails third\n",
        "",
    )

    assert [item.name for item in parsed] == [
        "first.test.ts | Suite A | passes first",
        "second.test.ts | Suite B | passes second",
    ]


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


def test_resume_ledger_treats_any_real_final_score_as_terminal(tmp_path):
    path = tmp_path / "results.json"
    records = [
        {"instance_id": "resolved", "final_grade": {"resolved": True}},
        {"instance_id": "unresolved", "final_grade": {"resolved": False}},
        {"instance_id": "grader-failed", "final_grade": {"error_code": "timeout"}},
        {"instance_id": "interrupted", "status": "failed"},
    ]

    atomic_write_json(str(path), records)

    assert load_run_results(str(path)) == records
    assert scored_instance_ids(records) == {"resolved", "unresolved"}
    assert not (tmp_path / "results.json.tmp").exists()


def test_valid_git_checkout_can_resume_even_before_it_has_edits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "source.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "source.txt")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    assert has_resumable_workspace(str(repo), base)
    assert not has_resumable_workspace(str(repo), "0" * 40)
    assert not has_resumable_workspace(str(tmp_path / "missing"), base)


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
