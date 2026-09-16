"""Known test-data corrections preserve candidate code and normal manager scoring."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import docker
import pytest

from agentevolver.benchmark import BenchmarkManager, Task
from agentevolver.benchmark.default.swebench import SWEBenchProBenchmark, SWEBenchProTaskRepairs


@pytest.fixture
def repaired_task(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Tests")
    git("config", "user.email", "tests@example.com")
    (repo / "pkg/testdata").mkdir(parents=True)
    (repo / "pkg/case_test.go").write_text("base tests\n")
    (repo / "pkg/testdata/existing.yml").write_text("old data\n")
    (repo / "app.py").write_text("old implementation\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (repo / "pkg/case_test.go").write_text("updated tests\n")
    fixtures = {"pkg/testdata/existing.yml": "updated data\n", "pkg/testdata/new.yml": "new data\n"}
    for name, content in fixtures.items():
        (repo / name).write_text(content)
    (repo / "app.py").write_text("reference implementation must not be copied\n")
    git("add", ".")
    git("commit", "-qm", "test revision")
    revision = git("rev-parse", "HEAD")
    git("reset", "--hard", base)
    # Untracked test data is absent from a fresh base checkout.
    assert not (repo / "pkg/testdata/new.yml").exists()
    (repo / "app.py").write_text("correct candidate\n")
    patch = git("diff") + "\n"
    git("reset", "--hard", base)
    row = dict(instance_id="sample", repo="owner/repo", base_commit=base,
               before_repo_set_cmd=f"git reset --hard {base}\ngit checkout {revision} -- pkg/case_test.go",
               problem_statement="fix the public behavior", fail_to_pass=["case"],
               selected_test_files_to_run=["case"])
    recipe = dict(id="fixture-repair-v1", repo="owner/repo", base_commit=base,
                  test_revision=revision, test_files=["pkg/case_test.go"], reason="missing test data",
                  fixtures={p: hashlib.sha256(v.encode()).hexdigest() for p, v in fixtures.items()})
    monkeypatch.setattr(SWEBenchProTaskRepairs, "REPAIRS", {"sample": recipe})

    grader = tmp_path / "grader"
    scripts = grader / "run_scripts/sample"
    scripts.mkdir(parents=True)
    (grader / "swe_bench_pro_eval.py").write_text("# test grader\n")
    (scripts / "run_script.sh").write_text(
        "python3 - <<'PY'\nfrom pathlib import Path\n"
        f"expected = {fixtures!r}\n"
        "ok = all(Path(p).read_text() == v for p,v in expected.items())\n"
        "ok = ok and Path('app.py').read_text() == 'correct candidate\\n'\n"
        "print('PASSED' if ok else 'FAILED')\nPY\n")
    (scripts / "parser.py").write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "status = Path(sys.argv[1]).read_text().strip()\n"
        "Path(sys.argv[3]).write_text(json.dumps({'tests':[{'name':'case','status':status}]}))\n")

    # Execute the real generated shell and parser against a temporary Git repository.
    # Only the Docker transport is replaced, keeping evaluation entirely via the manager.
    def run_container(image, **kwargs):
        mounted = Path(next(iter(kwargs["volumes"])))
        script = (mounted / "entryscript.sh").read_text()
        script = script.replace("/workspace", str(mounted)).replace("/app", str(repo))
        env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
        result = subprocess.run(["bash", "-c", script], capture_output=True, env=env)
        return types.SimpleNamespace(wait=lambda: {"StatusCode": result.returncode})

    client = types.SimpleNamespace(images=types.SimpleNamespace(pull=lambda *a, **k: None),
                                   containers=types.SimpleNamespace(run=run_container), close=lambda: None)
    monkeypatch.setattr(docker, "from_env", lambda: client)

    async def initialize(benchmark):
        benchmark._data_records = [copy.deepcopy(row)]

    monkeypatch.setattr(SWEBenchProBenchmark, "_initialize", initialize)
    return row, recipe, repo, patch, grader


@pytest.mark.asyncio
@pytest.mark.parametrize("correct", [True, False])
async def test_manager_counts_repaired_results_normally(repaired_task, tmp_path, correct):
    row, recipe, repo, patch, grader = repaired_task
    original_row = copy.deepcopy(row)
    if not correct:
        patch = patch.replace("+correct candidate", "+incorrect candidate")
    manager = BenchmarkManager()
    await manager.configure("swebench_pro", base_dir=str(tmp_path / "benchmark"))
    submission = dict(instance_id="sample", base_commit=row["base_commit"], patch=patch,
                      sha256=hashlib.sha256(patch.encode()).hexdigest())
    task = Task(task_id="sample", result=submission,
                extra={"workspace_dir": str(tmp_path / "session/workspace"), "grader_repo": str(grader)})
    result = await manager.eval("swebench_pro", task)
    assert result.score == int(correct)
    assert result.evaluation.status == ("passed" if correct else "failed")
    report = result.evaluation.details
    assert report["official_output_available"] is True
    assert report["grader_profile"] == "official_repaired"
    assert report["task_repair_applied"] is True
    assert report["task_repair_id"] == recipe["id"]
    assert (await manager.stats("swebench_pro")).correct == int(correct)
    assert row == original_row
    assert result.result == submission
    assert "reference implementation" not in (repo / "app.py").read_text()
    assert json.loads((Path(report["evidence_path"]) / "task_repair.json").read_text())["fixtures"] == recipe["fixtures"]
    assert "testdata" not in json.dumps(manager.task_payload("swebench_pro", row))
    await manager.cleanup("swebench_pro")


@pytest.mark.asyncio
async def test_bad_fixture_digest_stops_tests_and_is_unscored(repaired_task, tmp_path):
    row, recipe, _, patch, grader = repaired_task
    recipe["fixtures"]["pkg/testdata/new.yml"] = "0" * 64
    manager = BenchmarkManager()
    await manager.configure("swebench_pro", base_dir=str(tmp_path / "benchmark"))
    task = Task(task_id="sample", result=dict(instance_id="sample", base_commit=row["base_commit"],
                patch=patch, sha256=hashlib.sha256(patch.encode()).hexdigest()),
                extra={"workspace_dir": str(tmp_path / "session/workspace"), "grader_repo": str(grader)})
    result = await manager.eval("swebench_pro", task)
    assert result.score is None
    assert result.evaluation.status == "error"
    report = result.evaluation.details
    assert report["error_code"] == "test_data_repair_failed"
    assert report["task_repair_applied"] is False
    assert not (Path(report["evidence_path"]) / "workspace/output.json").exists()
    await manager.cleanup("swebench_pro")


@pytest.mark.parametrize("changed", ["repo", "base_commit", "before_repo_set_cmd"])
def test_changed_task_identity_requires_review(repaired_task, changed):
    row, *_ = repaired_task
    row[changed] = "changed"
    with pytest.raises(ValueError, match="review required"):
        SWEBenchProTaskRepairs.prepare(row)


def test_unknown_tasks_and_production_files_are_not_repaired(repaired_task):
    row, recipe, *_ = repaired_task
    unknown = {**row, "instance_id": "unknown"}
    prepared, repair = SWEBenchProTaskRepairs.prepare(unknown)
    assert prepared == unknown and prepared is not unknown and repair is None
    recipe["fixtures"]["app.py"] = "0" * 64
    with pytest.raises(ValueError, match="invalid fixture manifest"):
        SWEBenchProTaskRepairs.prepare(row)


@pytest.mark.asyncio
async def test_manager_resume_identity_includes_repair_catalog(repaired_task):
    _, recipe, _, _, grader = repaired_task
    manager = BenchmarkManager()
    options = {"grader_repo": str(grader), "grader_profile": "official"}
    initial = await manager.get_info("swebench_pro", evaluation_options=options)
    recipe["fixtures"]["pkg/testdata/new.yml"] = "1" * 64
    with pytest.raises(ValueError, match="grader_fingerprint"):
        await manager.get_info("swebench_pro", evaluation_options=options,
                               expected_evaluation=initial.evaluation)
    assert not await manager.is_loaded("swebench_pro")
