"""Differential checks against the checked-out upstream local-Docker evaluator."""
import ast
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentevolver.benchmark.default import swebench as grading

UPSTREAM = Path(__file__).resolve().parents[1] / "others/SWE-bench_Pro"


def upstream_namespace():
    scope = {"os": os, "json": json, "re": re}
    for path in [UPSTREAM / "helper_code/image_uri.py", UPSTREAM / "swe_bench_pro_eval.py"]:
        nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)
    return scope


@pytest.mark.parametrize("iid,repo", [
    ("instance_acme__repo-abc-vnan", "acme/repo"),
    ("instance_element-hq__element-web-other-vnan", "element-hq/element-web"),
    ("instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan", "element-hq/element-web"),
    ("instance_acme__repo-" + "a" * 180, "acme/repo"),
])
def test_image_naming_matches_upstream(iid, repo):
    expected = upstream_namespace()["get_dockerhub_image_uri"](iid, "jefzda", repo)
    assert grading._official_pro_image_ref({"instance_id": iid, "repo": repo}) == expected


@pytest.mark.parametrize("output", [None, {"tests": []},
    {"tests": [{"name": "target", "status": "PASSED"}]},
    {"tests": [{"name": "target", "status": "PASSED"}, {"name": "target", "status": "FAILED"}]},
])
def test_score_matches_upstream_main(output):
    row = {"fail_to_pass": "['target']", "pass_to_pass": "[]"}
    # Execute the exact scoring block rather than another copy of the implementation.
    source = (UPSTREAM / "swe_bench_pro_eval.py").read_text()
    start = source.index('                        passed_tests = ')
    end = source.index('\n                        eval_results[', start)
    import textwrap
    scope = {"output": output, "raw_sample": row}
    if output is None:
        expected = False  # Upstream main's output-is-None branch.
    else:
        exec(textwrap.dedent(source[start:end]), scope)
        expected = scope["result"]
    assert grading._official_pro_report(output, row)["resolved"] is expected


@pytest.mark.parametrize("output,exit_code,pull_fails", [
    ({"tests": [{"name": "target", "status": "PASSED"}]}, 1, False),
    ({"tests": [{"name": "target", "status": "FAILED"}]}, 0, True),
    (None, 1, False),
])
def test_runtime_and_evidence_match_upstream(tmp_path, monkeypatch, output, exit_code, pull_fails):
    iid = "instance_acme__repo-abc"
    repo = tmp_path / "grader"
    scripts = repo / "run_scripts" / iid
    scripts.mkdir(parents=True)
    (scripts / "run_script.sh").write_text("go test -race ./...\n")
    (scripts / "parser.py").write_text("# original parser\n")
    for kind in ("base_dockerfile", "instance_dockerfile"):
        p = repo / "dockerfiles" / kind / iid
        p.mkdir(parents=True)
        (p / "Dockerfile").write_text("ENV TEST_ENV=value\n")
    monkeypatch.chdir(repo)
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    row = {"instance_id": iid, "repo": "acme/repo", "base_commit": "abc",
           "before_repo_set_cmd": "setup\ngit checkout def -- target_test.go",
           "selected_test_files_to_run": "['target/foo,bar']",
           "fail_to_pass": "['target']", "pass_to_pass": "[]"}
    calls = []

    def pull(image, **kwargs):
        calls.append(("pull", image, kwargs))
        if pull_fails:
            raise RuntimeError("registry unavailable")

    def run(image, **kwargs):
        calls.append(("run", image, kwargs))
        mounted = Path(next(iter(kwargs["volumes"])))

        def wait():
            (mounted / "stdout.log").write_text('OK: 0 passed, 10 skipped\nFAIL other [build failed]\n')
            (mounted / "stderr.log").write_text("compiler warning\n")
            if output is not None:
                (mounted / "output.json").write_text(json.dumps(output))
            return {"StatusCode": exit_code}
        return SimpleNamespace(wait=wait)

    client = SimpleNamespace(images=SimpleNamespace(pull=pull, get=lambda image: calls.append(("get", image))),
                             containers=SimpleNamespace(run=run), close=lambda: None)
    sdk = SimpleNamespace(from_env=lambda: client)
    monkeypatch.setitem(sys.modules, "docker", sdk)
    scope = upstream_namespace()
    scope["docker"] = sdk
    upstream_output = scope["eval_with_docker"]("", row, str(tmp_path / "upstream"),
                                              "jefzda", str(repo / "run_scripts"))
    upstream_calls = list(calls)
    calls.clear()
    report = grading._run_official_pro(str(tmp_path / "session/workspace"), row, str(repo), "")
    assert report["resolved"] == grading._official_pro_report(upstream_output, row)["resolved"]
    assert "error_code" not in report  # Log-based reclassification must not change the score.
    assert [c[:2] for c in calls] == [c[:2] for c in upstream_calls]
    before, after = upstream_calls[-1][2].copy(), calls[-1][2].copy()
    before_mount, after_mount = before.pop("volumes"), after.pop("volumes")
    assert before == after
    assert list(before_mount.values()) == list(after_mount.values())
    for name in ("run_script.sh", "parser.py", "patch.diff", "entryscript.sh"):
        assert (Path(next(iter(before_mount))) / name).read_bytes() == (Path(next(iter(after_mount))) / name).read_bytes()
    assert Path(report["evidence_path"], "stdout.log").exists()


@pytest.mark.asyncio
async def test_default_benchmark_grading_routes_in_process_not_to_cli(monkeypatch, tmp_path):
    seen = []
    def evaluate(*args):
        seen.append(args)
        return {"resolved": True, "grader_protocol": grading.OFFICIAL_PRO_GRADER_PROTOCOL}
    monkeypatch.setattr(grading, "_run_official_pro", evaluate)
    def forbidden(*args, **kwargs):
        pytest.fail("official score must not use diagnostic classification")
    monkeypatch.setattr(grading, "_grade_report", forbidden)
    benchmark = grading.SWEBenchProBenchmark(base_dir=str(tmp_path))
    report = await benchmark._evaluate({"instance_id": "sample"}, "", {
        "workspace_dir": str(tmp_path / "workspace"), "grader_repo": "grader"})
    assert report["resolved"]
    assert seen == [(str(tmp_path / "workspace"), {"instance_id": "sample"}, "grader", "")]
