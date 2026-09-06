"""Manager lifecycle tests use real persistence/patches and simulated task resources."""

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentevolver.benchmark import BenchmarkManager, Task, EvaluationResult
from agentevolver.benchmark.default.exact_match import ExactMatchBenchmark
from agentevolver.benchmark.default.programbench import ProgramBenchmark
from agentevolver.benchmark.default.swebench import SWEBenchProBenchmark, SWEBenchVerifiedBenchmark
from agentevolver.utils.file_utils import atomic_json_update


@pytest.mark.asyncio
async def test_answer_lifecycle_freezes_and_recovers_statistics(tmp_path):
    manager = BenchmarkManager()
    await manager.configure("exact_match", base_dir=str(tmp_path))
    task = Task(task_id="one", input="question", ground_truth="42", result="42")
    ctx = await manager.prepare("exact_match", task)
    assert ctx.container_name is None
    assert "ground_truth" not in ctx.payload
    await manager.submit("exact_match", task)
    task.result = "changed by caller"
    await manager.eval("exact_match", task)
    assert task.result == "42" and task.score == 1
    await manager.cleanup("exact_match", task)
    assert Path(ctx.submission_path).is_file()
    restored = BenchmarkManager()
    await restored.configure("exact_match", base_dir=str(tmp_path), resume=True)
    stats = await restored.stats("exact_match")
    assert stats.correct == 1 and stats.attempted == 1
    assert (await restored.prepare("exact_match", Task(task_id="one"))).completed
    with pytest.raises(RuntimeError, match="cleanup active"):
        await restored.reset("exact_match")
    await restored.cleanup("exact_match", Task(task_id="one"))
    await restored.reset("exact_match", resume=True)
    assert (await restored.stats("exact_match")).correct == 1


@pytest.mark.asyncio
async def test_concurrent_submit_collects_once_across_manager_instances(tmp_path, monkeypatch):
    calls = []
    original = ExactMatchBenchmark._collect_submission

    async def collect(self, task, ctx, output):
        calls.append(output)
        await asyncio.sleep(0.01)
        return await original(self, task, ctx, output)

    monkeypatch.setattr(ExactMatchBenchmark, "_collect_submission", collect)
    managers = [BenchmarkManager(), BenchmarkManager()]
    tasks = [Task(task_id="one", result="first"), Task(task_id="one", result="second")]
    for manager, task in zip(managers, tasks):
        await manager.configure("exact_match", base_dir=str(tmp_path), resume=True)
        await manager.prepare("exact_match", task)
    await asyncio.gather(
        *(manager.submit("exact_match", task) for manager, task in zip(managers, tasks))
    )
    assert len(calls) == 1
    assert tasks[0].result == tasks[1].result == calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "tampered", "wrong_id"])
async def test_submission_damage_never_restarts_task(tmp_path, monkeypatch, damage):
    manager = BenchmarkManager()
    await manager.configure("exact_match", base_dir=str(tmp_path))
    task = Task(task_id="one", result="answer")
    ctx = await manager.prepare("exact_match", task)
    await manager.submit("exact_match", task)
    path = Path(ctx.submission_path)
    data = json.loads(path.read_text())
    if damage == "missing":
        path.unlink()
    else:
        data["answer" if damage == "tampered" else "instance_id"] = "bad"
        atomic_json_update(path, lambda _: data)
    prepare = AsyncMock()
    monkeypatch.setattr(ExactMatchBenchmark, "_prepare", prepare)
    restored = BenchmarkManager()
    await restored.configure("exact_match", base_dir=str(tmp_path), resume=True)
    with pytest.raises(ValueError):
        await restored.prepare("exact_match", Task(task_id="one"))
    prepare.assert_not_awaited()
    assert (await restored.stats("exact_match")).errors == 1


@pytest.mark.asyncio
async def test_new_run_does_not_reuse_an_old_answer(tmp_path):
    manager = BenchmarkManager()
    await manager.configure("exact_match", base_dir=str(tmp_path))
    await manager.submit("exact_match", Task(task_id="one", result="answer"))
    fresh = BenchmarkManager()
    await fresh.configure("exact_match", base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="existing frozen"):
        await fresh.prepare("exact_match", Task(task_id="one"))


@pytest.mark.asyncio
async def test_legacy_history_counts_pass_fail_and_error_without_rewriting(tmp_path, monkeypatch):
    records = [
        {"instance_id": "pass", "final_grade": {"resolved": True}},
        {"instance_id": "fail", "final_grade": {"resolved": False}},
        {"instance_id": "error", "final_grade": {"error_code": "timeout"}},
        {"instance_id": "interrupted", "status": "failed"},
    ]
    history = tmp_path / "results.json"
    history.write_text(json.dumps(records))
    before = history.read_bytes()
    monkeypatch.setattr(SWEBenchProBenchmark, "_initialize", AsyncMock())
    manager = BenchmarkManager()
    await manager.configure(
        "swebench_pro", base_dir=str(tmp_path / "state"), resume=True, history_path=str(history)
    )
    stats = await manager.stats("swebench_pro")
    assert (stats.correct, stats.wrong, stats.errors, stats.attempted) == (1, 1, 1, 3)
    assert set(stats.extra["completed_task_ids"]) == {"pass", "fail"}
    assert (await manager.prepare("swebench_pro", Task(task_id="pass"))).completed
    assert history.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("cls,name", [
    (SWEBenchProBenchmark, "swebench_pro"),
    (SWEBenchVerifiedBenchmark, "swebench_verified"),
])
async def test_failed_patch_collection_preserves_checkout_and_recovers_through_manager(
    tmp_path, monkeypatch, cls, name
):
    import subprocess

    workspace = tmp_path / "host-session" / "workspace"
    workspace.mkdir(parents=True)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(workspace), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    source = workspace / "answer.txt"
    source.write_text("before\n")
    git("add", ".")
    git("commit", "-qm", "base")
    row = {"instance_id": "one", "base_commit": git("rev-parse", "HEAD")}
    source.write_text("after\n")

    async def initialize(self):
        self._data_records = [row]

    collect = cls._collect_patch
    failure = Mock(side_effect=RuntimeError("git read-tree failed while collecting patch"))
    reclaim = Mock()
    monkeypatch.setattr(cls, "_initialize", initialize)
    monkeypatch.setattr(cls, "_prepare", AsyncMock())
    monkeypatch.setattr(cls, "_collect_patch", failure)
    monkeypatch.setattr(cls, "_reclaim_instance_disk", reclaim)
    grade = AsyncMock(return_value={"resolved": True})
    monkeypatch.setattr(cls, "_evaluate", grade)
    # The collector must use the host checkout, even with a container path in the environment.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTEVOLVER_TASK_WORKSPACE", "/workspace")
    manager = BenchmarkManager()
    await manager.configure(name, base_dir=str(tmp_path / "state"))
    task = Task(task_id="one")
    options = {"session_dir": str(workspace.parent), "workspace_dir": str(workspace)}
    ctx = await manager.prepare(name, task, context=options)
    with pytest.raises(RuntimeError, match="git read-tree"):
        await manager.submit(name, task, output={"status": "done"})
    await manager.cleanup(name, task, reclaim=True)
    reclaim.assert_not_called()
    grade.assert_not_awaited()
    assert source.read_text() == "after\n" and (workspace / ".git").is_dir()
    assert not Path(ctx.submission_path).exists()
    assert (await manager.stats(name)).errors == 1

    monkeypatch.setattr(cls, "_collect_patch", collect)
    await manager.prepare(name, task, context={**options, "resume": True})
    await manager.submit(name, task, output={"status": "done"})
    frozen = Path(ctx.submission_path).read_bytes()
    submission = json.loads(frozen)
    assert "-before\n+after" in submission["patch"]
    assert submission["sha256"] == hashlib.sha256(submission["patch"].encode()).hexdigest()
    # Evaluation uses the frozen artifact, not later task/workspace mutations.
    task.result = None
    source.write_text("later edits\n")
    result = await manager.eval(name, task)
    assert result.evaluation.status == "passed"
    assert grade.call_args.args[1] == submission["patch"]
    assert (await manager.stats(name)).errors == 0
    await manager.cleanup(name, task, reclaim=True)
    reclaim.assert_called_once()
    assert Path(ctx.submission_path).read_bytes() == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls,name,workspace",
    [
        (SWEBenchProBenchmark, "swebench_pro", "/workspace"),
        (SWEBenchVerifiedBenchmark, "swebench_verified", "/testbed"),
        (ProgramBenchmark, "programbench", "/workspace"),
    ],
)
async def test_coding_preparation_owns_mounts_and_preserves_sibling_tasks(
    tmp_path, monkeypatch, cls, name, workspace
):
    row = {
        "instance_id": "one",
        "base_commit": "base",
        "image": "verified-image",
        "dockerhub_tag": "tag",
        "image_name": "program-image",
    }

    async def initialize(self):
        self._data_records = [row, {**row, "instance_id": "two"}]
        if name == "programbench":
            self._instances = {r["instance_id"]: r for r in self._data_records}

    monkeypatch.setattr(cls, "_initialize", initialize)
    monkeypatch.setattr(cls, "_prepare_runtime_mounts", AsyncMock())
    monkeypatch.setattr(cls, "_grant_workspace", AsyncMock())
    monkeypatch.setattr(cls, "_restore_workspace_owner", AsyncMock())
    monkeypatch.setattr(
        cls, "_seed_workspace" if name == "programbench" else "_seed_workspace_async", AsyncMock()
    )
    acquire = AsyncMock(
        side_effect=[
            SimpleNamespace(container_name="one", start=AsyncMock()),
            SimpleNamespace(container_name="two", start=AsyncMock()),
        ]
    )
    release = AsyncMock()
    monkeypatch.setattr(
        "agentevolver.sandbox.sandbox_manager",
        SimpleNamespace(acquire=acquire, release=release, egress_audit=lambda _: {}),
    )
    manager = BenchmarkManager()
    await manager.configure(name, base_dir=str(tmp_path))
    one = await manager.prepare(name, Task(task_id="one"), context={"agent_on_host": False})
    two = await manager.prepare(name, Task(task_id="two"), context={"agent_on_host": False})
    assert one.agent_workspace == one.container_workspace == workspace
    assert acquire.call_args.kwargs["workdir"] == workspace
    assert acquire.call_args.kwargs["env"]["AGENTEVOLVER_TASK_WORKSPACE"] == workspace
    assert one.resource_key != two.resource_key
    await manager.cleanup(name, Task(task_id="one"), reclaim=True)
    await manager.cleanup(name, Task(task_id="one"), reclaim=True)
    assert release.await_count == 1
    assert release.call_args.kwargs["reuse_key"] == one.resource_key
    await manager.cleanup(name)
    assert release.await_count == 2


@pytest.mark.asyncio
async def test_prepare_cancellation_releases_partly_acquired_resources(tmp_path, monkeypatch):
    entered = asyncio.Event()
    released = []

    async def prepare(self, task, ctx, options):
        self._resources[task.task_id] = object()
        entered.set()
        await asyncio.Event().wait()

    async def stop(self, ctx):
        released.append(ctx.task_id)
        self._resources.pop(ctx.task_id, None)

    monkeypatch.setattr(ExactMatchBenchmark, "_prepare", prepare)
    monkeypatch.setattr(ExactMatchBenchmark, "_stop_task_resources", stop)
    manager = BenchmarkManager()
    await manager.configure("exact_match", base_dir=str(tmp_path))
    pending = asyncio.create_task(manager.prepare("exact_match", Task(task_id="one")))
    await asyncio.wait_for(entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert released == ["one"]
    await manager.cleanup("exact_match")
    assert released == ["one"]


@pytest.mark.asyncio
async def test_verified_seed_uses_its_own_image_directory(tmp_path, monkeypatch):
    bench = SWEBenchVerifiedBenchmark(base_dir=str(tmp_path))
    commands = []
    monkeypatch.setattr(bench, "_ensure_image", AsyncMock())

    async def command(argv, timeout):
        commands.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bench, "_provision_command", command)
    await bench._seed_workspace_async("image", "base", str(tmp_path / "workspace"))
    script = next(argv[-1] for argv in commands if argv[1] == "run")
    assert "cp -a /testbed/." in script
    assert "cp -a /app/." not in script


@pytest.mark.asyncio
async def test_programbench_protects_reference_permissions_when_resuming(tmp_path, monkeypatch):
    bench = ProgramBenchmark(base_dir=str(tmp_path))
    commands = []

    async def command(argv, timeout=300):
        commands.append(argv)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(bench, "_command", command)
    await bench._grant_workspace(
        str(tmp_path), protected=("workspace/executable", "workspace/reference_executable")
    )
    assert "chmod a-r /target/workspace/executable" in commands[0][-1]
    assert "chmod a-r /target/workspace/reference_executable" in commands[0][-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,cls",
    [("programbench", ProgramBenchmark), ("swebench_verified", SWEBenchVerifiedBenchmark)],
)
async def test_container_launchers_use_manager_lifecycle(tmp_path, monkeypatch, name, cls):
    import importlib
    from mmengine import Config
    from agentevolver.paths import P

    entry = importlib.import_module("examples.run_" + name)
    owner, session, workspace = (
        tmp_path / "owner",
        tmp_path / "owner/sessions/one",
        tmp_path / "owner/sessions/one/workspace",
    )
    extension = tmp_path / "extension"
    extension.mkdir()

    class Paths:
        def get(self, key, **kw):
            return {
                P.OWNER: owner,
                P.SESSION: session,
                P.SESSION_WORKSPACE: workspace,
                P.EXTENSION: extension,
            }[key]

        def resolve_under(self, base, path):
            return Path(base) / path

        def under(self, base, key, **kw):
            return (
                Path(base)
                / {P.PROJECT_RESULT: "result.json", P.PROJECT_EXTENSION: "extension"}[key]
            )

    class Monitor:
        def __init__(self, *a, **kw):
            pass

        def task(self, *a, **kw):
            pass

        def finish_task(self, *a, **kw):
            pass

        def close(self, *a, **kw):
            pass

    row = {"instance_id": "one", "image_name": "image", "image": "image", "base_commit": "base"}

    async def initialize(self):
        self._data_records = [row]
        if name == "programbench":
            self._instances = {"one": row}

    events = []

    async def seed(*args):
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "compile.sh").write_text("#!/bin/sh\ntrue\n")

    async def worker(*args):
        events.append("agent")
        atomic_json_update(session / "result.json", lambda _: {"status": "done"})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    async def release(*args, **kw):
        events.append("stop")

    async def grade(self, task):
        assert events == ["agent", "stop"]
        assert task.result["sha256"]
        events.append("grade")
        task.evaluation = EvaluationResult.from_report({"resolved": False})
        task.score = 0
        return task

    monkeypatch.setattr(
        entry,
        "config",
        Config(
            dict(
                tag="test",
                output_owner="one",
                extension_root=str(extension),
                project_root=str(tmp_path),
                WALL_CLOCK=10,
            )
        ),
    )
    monkeypatch.setattr(entry, "path_manager", Paths())
    manager = BenchmarkManager()
    monkeypatch.setattr(entry, "benchmark_manager", manager)
    monkeypatch.setattr(cls, "_initialize", initialize)
    monkeypatch.setattr(cls, "_prepare_runtime_mounts", AsyncMock())
    monkeypatch.setattr(cls, "_grant_workspace", AsyncMock())
    monkeypatch.setattr(cls, "_restore_workspace_owner", AsyncMock())
    monkeypatch.setattr(
        cls, "_seed_workspace" if name == "programbench" else "_seed_workspace_async", seed
    )
    if name != "programbench":
        monkeypatch.setattr(cls, "_collect_patch", lambda *a: "patch")
    monkeypatch.setattr(cls, "_eval", grade)
    monkeypatch.setattr(entry, "run_worker_command", worker)
    sandbox = SimpleNamespace(
        initialize=AsyncMock(),
        acquire=AsyncMock(return_value=SimpleNamespace(container_name="one", start=AsyncMock())),
        release=release,
        egress_audit=lambda _: {},
    )
    monkeypatch.setattr("agentevolver.sandbox.sandbox_manager", sandbox)
    if name == "programbench":
        monkeypatch.setattr(entry, "sandbox_manager", sandbox)
    monkeypatch.setattr("agentevolver.visual.BenchmarkMonitor", Monitor)
    args = entry.parse_args(
        ["--start", "0", "--end", "1", "--out", str(tmp_path / "run"), "--no-monitor"]
    )
    args.user_cfg_options = {"output_owner": "one"}
    assert await entry.run_launcher(args) == 0
    assert events == ["agent", "stop", "grade"]
    assert list(session.rglob("submission.json"))


@pytest.mark.asyncio
async def test_persistence_failure_and_stats_agree(tmp_path, monkeypatch):
    manager = BenchmarkManager()
    await manager.configure("exact_match", base_dir=str(tmp_path))

    def failure(*args):
        raise OSError("disk full")

    monkeypatch.setattr(ExactMatchBenchmark, "_record_evaluation", failure)
    task = await manager.eval("exact_match", Task(task_id="one", result="42", ground_truth="42"))
    assert task.evaluation.details["error_code"] == "persistence_failed"
    assert task.score is None
    stats = await manager.stats("exact_match")
    assert stats.errors == 1 and stats.correct == 0


@pytest.mark.asyncio
async def test_cancellation_during_sandbox_start_is_owned(tmp_path, monkeypatch):
    row = {"instance_id": "one", "base_commit": "base", "image": "image"}

    async def initialize(self):
        self._data_records = [row]

    entered = asyncio.Event()

    async def start():
        entered.set()
        await asyncio.Event().wait()

    release = AsyncMock()
    monkeypatch.setattr(SWEBenchVerifiedBenchmark, "_initialize", initialize)
    for hook in (
        "_seed_workspace_async",
        "_grant_workspace",
        "_prepare_runtime_mounts",
        "_restore_workspace_owner",
    ):
        monkeypatch.setattr(SWEBenchVerifiedBenchmark, hook, AsyncMock())
    acquire = AsyncMock(return_value=SimpleNamespace(container_name="one", start=start))
    monkeypatch.setattr(
        "agentevolver.sandbox.sandbox_manager",
        SimpleNamespace(acquire=acquire, release=release, egress_audit=lambda _: {}),
    )
    manager = BenchmarkManager()
    await manager.configure("swebench_verified", base_dir=str(tmp_path))
    pending = asyncio.create_task(manager.prepare("swebench_verified", Task(task_id="one")))
    await asyncio.wait_for(entered.wait(), 1)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert acquire.call_args.kwargs["start"] is False
    release.assert_awaited_once()
    await manager.cleanup("swebench_verified")
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_cleanup_retains_ownership_until_retry(tmp_path, monkeypatch):
    async def prepare(self, task, ctx, options):
        self._resources[task.task_id] = SimpleNamespace(container_name="resource")
        self._shared_paths.add(str(tmp_path / "shared"))

    monkeypatch.setattr(ExactMatchBenchmark, "_prepare", prepare)
    restore = AsyncMock()
    monkeypatch.setattr(ExactMatchBenchmark, "_restore_workspace_owner", restore)
    release = AsyncMock(side_effect=[RuntimeError("daemon unavailable"), True])
    monkeypatch.setattr(
        "agentevolver.sandbox.sandbox_manager",
        SimpleNamespace(release=release, egress_audit=lambda _: {}),
    )
    manager = BenchmarkManager()
    await manager.configure("exact_match", base_dir=str(tmp_path))
    await manager.prepare("exact_match", Task(task_id="one"))
    with pytest.raises(RuntimeError, match="shared permissions retained"):
        await manager.cleanup("exact_match")
    restore.assert_not_awaited()
    await manager.cleanup("exact_match")
    assert release.call_args.kwargs["resource_id"] == "resource"
    restore.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["instance_id", "sha256"])
async def test_audit_preserves_candidate_identity_for_manager_validation(tmp_path, monkeypatch, field):
    import others.swe_grader_audit as audit
    row = {"instance_id": "one", "base_commit": "base", "patch": "reference"}
    candidate = {"instance_id": "one", "base_commit": "base", "patch": "candidate",
                 "sha256": hashlib.sha256(b"candidate").hexdigest()}
    candidate[field] = "wrong"
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate))
    grader = tmp_path / "grader"
    grader.mkdir()
    (grader / "swe_bench_pro_eval.py").touch()
    monkeypatch.setattr(audit.pq, "read_table", lambda *a, **kw: SimpleNamespace(to_pylist=lambda: [row]))
    async def initialize(self):
        self._data_records = [row]
    monkeypatch.setattr(SWEBenchProBenchmark, "_initialize", initialize)
    evaluate = AsyncMock()
    monkeypatch.setattr(SWEBenchProBenchmark, "_evaluate", evaluate)
    monkeypatch.setattr(audit, "benchmark_manager", BenchmarkManager())
    assert await audit.main(["--task-id", "one", "--out", str(tmp_path / "audit"),
                             "--submission", str(path), "--grader-repo", str(grader)]) == 1
    evaluate.assert_not_awaited()
