"""Focused tests for host-side SWE-bench Pro patch collection."""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import examples.run_swebench_pro as swebench_pro
from examples.run_swebench_pro import (
    EVALUATION_PROTOCOL,
    _argv_safe_run_script,
    _as_list,
    _build_entryscript,
    _grade_report,
    _parser_with_fail_boundaries,
    _restore_test_fixtures,
    atomic_write_json,
    collect_patch,
    freeze_submission,
    grader_fingerprint,
    has_resumable_workspace,
    load_run_results,
    load_submission,
    parse_cfg_options,
    pending_submission,
    scored_instance_ids,
    solver_environment,
    validate_run_protocol,
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


@pytest.mark.parametrize(("text", "tests", "code"), [
    ("testing: warning: no tests to run\n=== RUN TestTarget\n--- FAIL: TestTarget (0s)",
     [{"name": "TestTarget", "status": "FAILED"}], None),
    ("testing: warning: no tests to run\n# pkg [pkg.test]\na_test.go:1: undefined: method",
     [], "test_build_failed"),
    ("created: 384/384 workers\nINTERNALERROR> OSError: [Errno 24] Too many open files",
     [], "test_runner_failed"),
    ("open testdata/missing.yml: no such file or directory\n--- FAIL: TestTarget (0s)",
     [{"name": "TestOther", "status": "PASSED"}], None),
    ("testing: warning: no tests to run\nPASS\nok pkg [no tests to run]", [], "test_selection_failed"),
    ("", [], "no_tests_executed"),
    ("Tests: 1 failed, 4 passed, 5 total", [], "test_results_missing"),
])
def test_grade_diagnostics_distinguish_execution_failures(text, tests, code):
    report = _grade_report({"tests": tests}, {"fail_to_pass": ["TestTarget"]}, {"stdout.log": text})

    assert report.get("error_code") == code
    assert report["resolved"] is False
    assert "TestTarget" not in report.get("error_details", "")


def test_grader_setup_failure_overrides_parser_output():
    report = _grade_report(
        {"tests": [{"name": "target", "status": "PASSED"}]},
        {"fail_to_pass": ["target"]},
        {"status.json": json.dumps({"stage": "patch", "exit_code": 1})},
    )
    assert report["error_code"] == "patch_failed"
    assert report["resolved"] is False


def test_test_build_failure_requires_compatibility_review_not_host_blame():
    report = _grade_report(
        {"tests": [{"name": "other", "status": "PASSED"}]},
        {"fail_to_pass": ["target"]},
        {"stdout.log": "FAIL pkg [build failed]", "stderr.log": "unknown field Missing in type Config"},
        profile="official",
    )
    assert report["error_code"] == "test_build_failed"
    assert report["failure_kind"] == "test_compatibility"
    assert not report["resolved"]


def test_missing_fixture_hint_does_not_reclassify_assertion_or_passing_negative_test():
    for result in ("FAILED", "PASSED"):
        report = _grade_report(
            {"tests": [{"name": "target", "status": result}]},
            {"fail_to_pass": ["target"]},
            {"stdout.log": "open testdata/input.yml: no such file or directory"},
            profile="official",
        )
        assert "error_code" not in report
        assert bool(report.get("diagnostic_notes")) == (result == "FAILED")
        assert report["resolved"] == (result == "PASSED")


@pytest.mark.parametrize("noise", [
    "=== RUN TestMissingFile\nstat testdata/absent.pem: no such file or directory\n--- PASS: TestMissingFile",
    "# pkg [pkg.test]\nsqlite3.c: warning: deprecated declaration",
    "=== RUN TestResourceError\nResource temporarily unavailable\n--- PASS: TestResourceError",
])
def test_passing_negative_tests_and_compiler_warnings_are_not_harness_errors(noise):
    report = _grade_report({"tests": [{"name": "target", "status": "PASSED"}]},
                           {"fail_to_pass": ["target"]}, {"stdout.log": noise}, profile="official")
    assert report["resolved"] is True
    assert "error_code" not in report


def test_official_entryscript_matches_upstream_generator(tmp_path):
    source = Path(swebench_pro.DEFAULT_GRADER_REPO, "swe_bench_pro_eval.py").read_text()
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "create_entryscript")
    namespace = {"load_base_docker": lambda _: "ENV FOO=bar",
                 "instance_docker": lambda _: "ENV BAZ=qux"}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "upstream", "exec"), namespace)
    for kind, value in [("base_dockerfile", "ENV FOO=bar"), ("instance_dockerfile", "ENV BAZ=qux")]:
        directory = tmp_path / "dockerfiles" / kind / "sample"
        directory.mkdir(parents=True)
        (directory / "Dockerfile").write_text(value)
    row = dict(instance_id="sample", base_commit="abc", before_repo_set_cmd="setup\ngit checkout def -- a_test.go",
               selected_test_files_to_run="['TestA', 'TestB/foo,bar']")
    official = _build_entryscript(row, str(tmp_path), profile="official")
    assert official == namespace["create_entryscript"](row)
    assert "restore_fixtures" not in official
    assert "PYTEST_XDIST_AUTO_NUM_WORKERS" not in official


def test_grader_fingerprint_detects_changed_and_added_assets(tmp_path):
    script = tmp_path / "run_script.sh"
    script.write_text("first")
    first = grader_fingerprint(str(tmp_path))
    script.write_text("second")
    second = grader_fingerprint(str(tmp_path))
    assert second != first
    (tmp_path / "parser.py").write_text("new")
    assert grader_fingerprint(str(tmp_path)) != second


@pytest.mark.parametrize("receipt,previous", [
    (True, {}), (False, {"final_grade": {"error_code": "timeout"}}),
    (False, {"submission_sha256": "known"}),
])
def test_missing_frozen_submission_cannot_restart_solver(tmp_path, receipt, previous):
    marker = tmp_path / "submission_receipt.json"
    if receipt:
        marker.write_text('{"sha256":"known"}')
    with pytest.raises(ValueError, match="refusing to restart"):
        pending_submission(str(tmp_path / "submission.json"), str(marker),
                           {"instance_id": "sample", "base_commit": "base"}, previous)


def test_grader_fixture_repair_is_disclosed_without_leaking_names():
    report = _grade_report(
        {"tests": [{"name": "target", "status": "PASSED"}]},
        {"fail_to_pass": ["target"]}, {"fixtures.json": '["private/testdata/answer.json"]'},
    )
    assert report["resolved"] is True
    assert report["fixture_files_restored"] == 1
    assert report["leaderboard_comparable"] is False
    assert "answer.json" not in json.dumps(report)


def test_fixture_restore_excludes_reference_code_and_existing_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "impl.go").write_text("baseline")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    files = {
        "pkg/testdata/input.yml": "fixture", "pkg/testdata/existing.json": "reference",
        "pkg/testdata/helper.go": "reference code", "prod/data.json": "production data",
        "other/testdata/input.yml": "unrelated", "pkg/a_test.go": "test code",
    }
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    (repo / "pkg/testdata/link.json").symlink_to("../../impl.go")
    (repo / "impl.go").write_text("reference implementation")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "reference")
    revision = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", base)
    (repo / "pkg/testdata").mkdir(parents=True, exist_ok=True)
    (repo / "pkg/testdata/existing.json").write_text("agent data")

    restored = _restore_test_fixtures(str(repo), base, f"git checkout {revision} -- pkg/a_test.go")

    assert restored == ["pkg/testdata/input.yml"]
    assert (repo / "pkg/testdata/input.yml").read_text() == "fixture"
    assert (repo / "pkg/testdata/existing.json").read_text() == "agent data"
    assert (repo / "impl.go").read_text() == "baseline"
    assert not (repo / "pkg/testdata/helper.go").exists()
    assert not (repo / "prod/data.json").exists()
    assert not (repo / "pkg/a_test.go").exists()
    assert not (repo / "pkg/testdata/link.json").is_symlink()


def test_ansible_worker_limit_preserves_explicit_configuration():
    script = "python bin/ansible-test units -v\npython bin/ansible-test units --num-workers 1 -v"
    bounded = _argv_safe_run_script(script)
    assert "units --num-workers 2 -v" in bounded
    assert "units --num-workers 1 -v" in bounded
    assert _argv_safe_run_script(bounded) == bounded


@pytest.mark.parametrize("failure_stage", ["patch", "tests_restore", "tests"])
def test_entryscript_stops_on_setup_errors_and_records_stage(tmp_path, failure_stage):
    # Translate only sandbox paths to a private test directory, then execute the real
    # generated shell with a deliberately invalid patch. Tests must not run unpatched.
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "file.txt").write_text("base")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (repo / "file.txt").write_text("changed")
    patch = _git(repo, "diff") + "\n"
    _git(repo, "restore", "file.txt")
    (workspace / "patch.diff").write_text("invalid patch" if failure_stage == "patch" else patch)
    (workspace / "restore_fixtures.py").write_text("pass\n")
    (workspace / "run_script.sh").write_text(
        'echo workers=$PYTEST_XDIST_AUTO_NUM_WORKERS\nexit 1\n')
    (workspace / "parser.py").write_text(
        "import sys\nopen(sys.argv[3], 'w').write('{\"tests\": []}')\n")
    script = _build_entryscript({
        "instance_id": "sample", "base_commit": _git(repo, "rev-parse", "HEAD"),
        "before_repo_set_cmd": "false" if failure_stage == "tests_restore" else "true",
    }, str(tmp_path))
    script = script.replace("/app", str(repo)).replace("/workspace", str(workspace))
    script = script.replace("python ", sys.executable + " ")
    # Do not alter the developer's global Git configuration during this shell test.
    script = script.replace("git config --global", "git config --local")
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    status = json.loads((workspace / "status.json").read_text())
    if failure_stage == "tests":
        assert result.returncode == 0, result.stderr
        assert status == {"stage": "complete", "exit_code": 0, "test_exit": 1, "parser_exit": 0}
        assert (workspace / "stdout.log").read_text() == "workers=2\n"
        assert json.loads((workspace / "output.json").read_text()) == {"tests": []}
    else:
        assert result.returncode != 0
        assert status["stage"] == failure_stage, result.stderr
        assert status["exit_code"] != 0
        assert not (workspace / "stdout.log").exists()


@pytest.mark.asyncio
async def test_grader_evidence_keeps_full_json_and_numeric_sequence(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bridge = tmp_path / "eval_bridge"
    bridge.mkdir()
    (bridge / "grader-9.entry.log").write_text("old")
    (bridge / "grader-10.entry.log").write_text("latest")
    payload = json.dumps({"tests": [{"name": "large" * 50000, "status": "FAILED"}]})

    class Sandbox:
        async def read_file(self, path):
            if path.endswith("output.json"):
                return payload
            return ""

    evidence = await swebench_pro._save_grader_evidence(
        Sandbox(), str(workspace), types.SimpleNamespace(stdout="first cause", stderr=""))
    assert evidence["output.json"] == payload
    assert json.loads((bridge / "grader-11.output.json").read_text()) == json.loads(payload)


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


def test_submission_is_frozen_and_tampering_is_rejected(tmp_path, monkeypatch):
    path = str(tmp_path / "submission.json")
    row = {"instance_id": "sample", "base_commit": "base"}
    monkeypatch.setattr(swebench_pro, "collect_patch", lambda *_: "original patch")
    original = freeze_submission(path, "unused", row, {"status": "failed"})
    monkeypatch.setattr(swebench_pro, "collect_patch", lambda *_: pytest.fail("must not recollect"))
    assert freeze_submission(path, "unused", row, {}) == original
    assert original["agent_result"]["status"] == "failed"
    with pytest.raises(ValueError):
        load_submission(path, "other-instance", "base")
    atomic_write_json(path, {**original, "patch": "changed after submission"})
    with pytest.raises(ValueError):
        load_submission(path, "sample", "base")


def test_solver_does_not_inherit_grader_access():
    env = {"PATH": "/bin", "AGENTEVOLVER_EVAL_BRIDGE": "/private",
           "AGENTEVOLVER_EVAL_BUDGET": "6"}
    assert solver_environment(env) == {"PATH": "/bin"}
    assert "AGENTEVOLVER_EVAL_BRIDGE" in env  # caller environment is unchanged


def test_legacy_and_existing_results_cannot_be_overwritten():
    with pytest.raises(ValueError, match="legacy"):
        validate_run_protocol({}, resume=True, existing=True)
    with pytest.raises(ValueError, match="existing run"):
        validate_run_protocol({}, resume=False, existing=True)
    validate_run_protocol({}, resume=False, existing=False)
    validate_run_protocol({"evaluation_protocol": EVALUATION_PROTOCOL}, resume=True, existing=True)


@pytest.mark.asyncio
async def test_empty_submission_is_a_failure_not_a_retryable_harness_error():
    report = await swebench_pro._grade_once(
        "unused", {"fail_to_pass": ["target"], "instance_id": "empty"}, "unused", patch="")
    assert report["resolved"] is False
    assert "error_code" not in report


@pytest.mark.asyncio
async def test_launcher_grades_after_exit_and_resume_never_restarts_submitted_agent(tmp_path, monkeypatch):
    import agentevolver.sandbox as sandbox_module
    import agentevolver.visual as visual_module
    monkeypatch.setenv("AGENTEVOLVER_EXTENSION_ROOT", str(tmp_path / "old-global"))

    class Config(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    owner = tmp_path / "owner"
    session = owner / "sessions" / "sample"
    workspace = session / "workspace"
    P = swebench_pro.P

    class Paths:
        def get(self, key, **kwargs):
            return {P.OWNER: owner, P.SESSION: session, P.SESSION_WORKSPACE: workspace,
                    P.EXTENSION: tmp_path / "extension"}[key]

        def resolve_under(self, base, name):
            return Path(base) / name

        def under(self, base, key):
            return Path(base) / {P.PROJECT_RESULT: "result.json", P.PROJECT_EXTENSION: "extension"}[key]

    class Monitor:
        def __init__(self, *args, **kwargs): pass
        def task(self, *args, **kwargs): pass
        def finish_task(self, *args): pass
        def close(self, *args): pass

    row = {"instance_id": "sample", "base_commit": "base"}
    args = types.SimpleNamespace(
        resume=False, out=str(tmp_path / "run"), config="sample.py", task_ids=None,
        start=0, end=1, user_cfg_options={"output_owner": "sample"},
        grader_repo=str(tmp_path / "grader"), concurrency=1, provision_concurrency=1,
        no_monitor=True, reclaim_disk=False, task_file="task.html",
    )
    Path(args.grader_repo).mkdir()
    (Path(args.grader_repo) / "swe_bench_pro_eval.py").touch()
    events = []

    async def solve(*_args, **kwargs):
        events.append("solve")
        # Local verification failed: it must still submit and receive a final score.
        atomic_write_json(str(session / "result.json"), {"status": "failed", "error": "local test failed"})
        return 1

    async def release(*args, **kwargs):
        events.append("release")

    def collect(*args):
        assert events[-1] == "release"
        events.append("freeze")
        return "unchanged frozen patch"

    async def grade(*args, patch, profile):
        assert profile == "official"
        assert patch == "unchanged frozen patch"
        assert events.count("solve") == 1
        events.append("grade")
        return {"error_code": "timeout"} if events.count("grade") == 1 else {"resolved": False}

    monkeypatch.setattr(swebench_pro, "config", Config(tag="test", output_owner="sample", WALL_CLOCK=10))
    monkeypatch.setattr(swebench_pro, "path_manager", Paths())
    monkeypatch.setattr(visual_module, "BenchmarkMonitor", Monitor)
    monkeypatch.setattr(sandbox_module, "sandbox_manager", types.SimpleNamespace(
        acquire=AsyncMock(return_value=types.SimpleNamespace(container_name="test")),
        release=release, egress_audit=lambda _: {},
    ))
    for name in ["check_shared_roots_readable", "restore_ownership", "grant_to_container_user"]:
        monkeypatch.setattr(swebench_pro, name, lambda *args: None)
    monkeypatch.setattr(swebench_pro, "image_ref", lambda _: "test-image")
    monkeypatch.setattr(swebench_pro, "load_instances", AsyncMock(return_value=[row]))
    monkeypatch.setattr(swebench_pro, "ensure_image", AsyncMock())
    monkeypatch.setattr(swebench_pro, "seed_workspace_async", AsyncMock())
    monkeypatch.setattr(swebench_pro, "run_inner_process", solve)
    monkeypatch.setattr(swebench_pro, "collect_patch", collect)
    monkeypatch.setattr(swebench_pro, "_grade_once", grade)

    assert await swebench_pro.run_launcher(args) == 1  # grader failure, artifact survives
    args.resume = True
    artifact = session / "submission.json"
    frozen = artifact.read_text()
    artifact.unlink()
    assert await swebench_pro.run_launcher(args) == 1  # missing artifact is NOT a new attempt
    assert events == ["solve", "release", "freeze", "grade"]
    artifact.write_text(frozen)
    assert await swebench_pro.run_launcher(args) == 0  # retry only the grader
    assert await swebench_pro.run_launcher(args) == 0  # unresolved is terminal too
    assert events == ["solve", "release", "freeze", "grade", "grade"]
    result = load_run_results(str(Path(args.out) / "results.json"))[0]
    assert result["grader_evals"] == 0
    assert result["evaluation_protocol"] == EVALUATION_PROTOCOL
    assert args.user_cfg_options["extension_root"] == str(owner / "extension")
    assert json.loads((Path(args.out) / "run_state.json").read_text())["grader_profile"] == "official"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["official", "diagnostic"])
async def test_grader_profile_controls_uploaded_scripts(tmp_path, monkeypatch, profile):
    import agentevolver.sandbox as sandbox_module
    scripts = {"run_script.sh": "python bin/ansible-test units -v\n",
               "parser.py": 'if line.startswith("PASS"):\n    pass\n'}
    writes = {}

    class Sandbox:
        async def write_file(self, path, data): writes[path] = data
        async def run_command(self, *args, **kwargs): return types.SimpleNamespace(stdout="", stderr="")
        async def read_file(self, path):
            if path == "/workspace/output.json":
                return '{"tests":[{"name":"target","status":"PASSED"}]}'
            raise FileNotFoundError(path)

    monkeypatch.setattr(sandbox_module, "sandbox_manager", types.SimpleNamespace(
        acquire=AsyncMock(return_value=Sandbox()), release=AsyncMock()))
    monkeypatch.setattr(swebench_pro, "image_ref", lambda _: "unused")
    monkeypatch.setattr(swebench_pro, "_load_script", lambda repo, iid, name: scripts[name])
    report = await swebench_pro._grade_once(
        str(tmp_path / "workspace"), {"instance_id": "sample", "base_commit": "base", "fail_to_pass": ["target"]},
        str(tmp_path), patch="test-patch", profile=profile)
    assert report["resolved"] is True
    assert report["grader_profile"] == profile
    if profile == "official":
        assert writes["/workspace/run_script.sh"] == scripts["run_script.sh"]
        assert writes["/workspace/parser.py"] == scripts["parser.py"]
        assert "restore_fixtures" not in writes["/workspace/entryscript.sh"]
    else:
        assert "--num-workers 2" in writes["/workspace/run_script.sh"]
        assert '"FAIL"' in writes["/workspace/parser.py"]
        assert report["leaderboard_comparable"] is False


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
    started = asyncio.Event()
    commands = []

    async def command(argv, timeout):
        commands.append(argv)
        if argv[1] == "run":
            started.set()
            await asyncio.Event().wait()
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(swebench_pro, "ensure_image", AsyncMock())
    monkeypatch.setattr(swebench_pro, "_provision_command", command)
    monkeypatch.setattr(swebench_pro.os, "makedirs", lambda *a, **kw: None)
    task = asyncio.create_task(swebench_pro.seed_workspace_async("image", "commit", "/workspace"))
    await asyncio.wait_for(started.wait(), 1)

    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert commands[-1][:3] == ["docker", "rm", "-f"]
    assert commands[0] == commands[-1]
    assert commands[-1][-1].startswith("agentevolver-seed-")


@pytest.mark.asyncio
async def test_provision_command_cancellation_reaps_client(monkeypatch):
    started = asyncio.Event()
    async def communicate():
        started.set()
        await asyncio.Event().wait()
    process = types.SimpleNamespace(pid=12345, communicate=communicate, wait=AsyncMock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    killed = []
    monkeypatch.setattr(swebench_pro.os, "killpg", lambda *args: killed.append(args))
    task = asyncio.create_task(swebench_pro._provision_command(["docker", "pull", "image"], 10))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed == [(12345, swebench_pro.signal.SIGKILL)]
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_seed_reset_failure_cannot_be_hidden_by_chown(monkeypatch, tmp_path):
    commands = []
    async def command(argv, timeout):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(swebench_pro, "ensure_image", AsyncMock())
    monkeypatch.setattr(swebench_pro, "_provision_command", command)
    await swebench_pro.seed_workspace_async("image", "base", str(tmp_path))
    script = next(argv[-1] for argv in commands if argv[1] == "run")
    # Stub every mutating command. Verify shell failure propagation without touching Docker.
    stub = 'rm() { :; }; cp() { :; }; git() { if [ "$1" = reset ]; then return 17; fi; }; chown() { echo UNEXPECTED; }; '
    result = subprocess.run(["sh", "-c", stub + script.replace("/seed", str(tmp_path))], capture_output=True, text=True)
    assert result.returncode == 17
    assert "UNEXPECTED" not in result.stdout
