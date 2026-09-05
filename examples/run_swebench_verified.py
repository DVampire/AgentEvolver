"""Run SWE-bench Verified tasks and submit frozen patches to BenchmarkManager.

The launcher owns task scheduling and agent lifecycle. SWEBenchVerifiedBenchmark
owns the host-side official harness; hidden grading never returns to an agent.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

from agentevolver.benchmark.default.swebench import SWEBenchVerifiedBenchmark
from agentevolver.benchmark import benchmark_manager
from agentevolver.benchmark.types import Task
from examples.run_swebench_pro import collect_patch, freeze_submission, load_submission
from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.paths import P, path_manager

# Reuse the proven, benchmark-agnostic host machinery from the ProgramBench launcher rather
# than duplicate it. Importing is safe: its main() is guarded by __main__, and module-level
# code only computes paths/constants.
from examples.run_programbench import (  # noqa: E402
    _LANDING_MARGIN_SECONDS,
    CONTAINER_USER,
    _summarise_spend,
    bind_task_workspace,
    check_shared_roots_readable,
    grant_to_container_user,
    host_repo_root,
    interpreter_prefix,
    restore_ownership,
)

root = host_repo_root()

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
#: HuggingFace dataset — the NEW-org SWE-bench Verified (500 instances, all Python) that the
#: swebench>=5 harness consumes: it ships the per-instance ``image`` name, ``eval_script``,
#: ``log_parser`` and ``eval_type`` that grading needs, so no separate grader checkout exists.
HF_DATASET = "SWE-bench/SWE-bench_Verified"

#: The repository lives at /testbed in every instance image, installed editable into the
#: image's ``testbed`` conda env — so the agent must edit *there* for a local test run to see
#: its change, and the grader (image-native) resets and tests there too.
CONTAINER_APP = "/testbed"
CONTAINER_REPO = "/AgentEvolver"

DEFAULT_TASK_FILE = os.path.join(root, "examples", "tasks", "swebench_verified_resolution.html")


#: The ONLY instance fields that may enter the container. Everything else in the row is the
#: answer key or host-side scoring metadata and is withheld. Order fixes the task-doc slots.
#: SWE-bench Verified carries no requirements/interface — problem_statement is the whole spec.
#: Derived from the benchmark class rather than restated here. Which fields may
#: enter a container IS the GT-safety design, and a second copy of that list is a
#: second thing to keep in step.
_SAFE_FIELDS = SWEBenchVerifiedBenchmark.model_fields["safe_fields"].default

_FORWARDED_ENV = (
    "OPENAI_API_KEY", "OPENAI_API_BASE",
    "ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE",
    "GOOGLE_API_KEY", "GOOGLE_API_BASE",
    "OPENROUTER_API_KEY", "OPENROUTER_API_BASE",
    "LLM_HUB_API_KEY", "LLM_HUB_API_BASE",
)


# --------------------------------------------------------------------------- #
# Dataset + GT-safe field filtering
# --------------------------------------------------------------------------- #
def _benchmark():
    """The one loader for this dataset, shared with the benchmark registry.

    This used to call `load_dataset` here, which put the rows in the HuggingFace cache
    instead of under `datasets/` where every other benchmark keeps its data — and stated
    a second time, in this file, which fields an agent may see. That list is the whole
    GT-safety design, and a copy of it is a copy that can drift.
    """
    return SWEBenchVerifiedBenchmark(base_dir="output/benchmark/swebench_verified")


async def load_instances() -> list:
    """Load the SWE-bench Verified split as a list of dict rows (full, incl. oracle)."""

    benchmark = _benchmark()
    await benchmark.initialize()
    await benchmark_manager.register(benchmark, override=True)
    return benchmark.instances()


def _safe_instance(row: dict) -> dict:
    """The subset of a row the container may see — never the gold patch or the tests."""
    return {key: row.get(key, "") for key in _SAFE_FIELDS}


def image_ref(row: dict) -> str:
    """The instance's eval image, straight from the dataset (swebench/sweb.eval.x86_64.…)."""
    return row["image"]


def select_instances(instances, task_ids=None, start=None, end=None):
    """Select a subset by id list or index range. task_ids wins over start/end."""
    if not task_ids and start is None and end is None:
        raise ValueError("select_instances requires task_ids or start/end")
    if task_ids:
        by_id = {i["instance_id"]: i for i in instances}
        unknown = [t for t in task_ids if t not in by_id]
        if unknown:
            logger.warning(f"| ⚠️ Unknown instance_ids ignored: {unknown}")
        return [by_id[t] for t in task_ids if t in by_id]
    return list(instances[start:end])


# --------------------------------------------------------------------------- #
# Task document
# --------------------------------------------------------------------------- #
def build_task_content(instance_full: dict, task_file=None, task_log_root=None):
    """Resolve the task document, substituting the SAFE fields only.

    ``instance_full`` may be the full row; only ``_SAFE_FIELDS`` are read from it, so the
    oracle can never reach the document by accident.
    """
    from agentevolver.task import load_task_document
    from agentevolver.visual import render_task_page

    safe = _safe_instance(instance_full)
    path = task_file or DEFAULT_TASK_FILE
    doc = load_task_document(path)
    slots = {
        "{repository}": str(safe.get("repo", "")),
        "{problem_statement}": str(safe.get("problem_statement", "")),
    }

    content = doc.content
    for key, value in slots.items():
        if key not in content:
            logger.warning(f"| ⚠️ Task document has no {key} placeholder: {path}")
        content = content.replace(key, value)

    metadata = {"task_doc": doc.source_path, "task_kind": doc.type}
    if task_log_root:
        html_body = doc.html_body
        for key, value in slots.items():
            html_body = html_body.replace(key, value)
        view_path = str(path_manager.under(
            task_log_root,
            P.LOG_TASK_VIEW,
            filename=f"task_view_{safe.get('instance_id', 'task')}.html",
        ))
        os.makedirs(task_log_root, exist_ok=True)
        render_task_page(html_body, view_path, title=doc.title)
        metadata["task_view"] = view_path

    return content, [doc.source_path], metadata


# --------------------------------------------------------------------------- #
# Patch collection + grading (host side — has the oracle)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Per-instance disk reclaim (for large runs on a full volume)
# --------------------------------------------------------------------------- #
def _reclaim_instance_disk(workspace_dir, image_ref_str) -> None:
    """Free one graded instance's disk: its seeded workspace and its (unique) image.

    Runs in a worker thread. The workspace is container-uid-owned, so its contents are
    removed through a throwaway root container before the (host-owned) top dir is dropped.
    The image's dockerhub_tag is unique to the instance, so ``docker rmi`` frees it without
    affecting any other in-flight instance. Best-effort — never raises.
    """
    if workspace_dir and os.path.isdir(workspace_dir):
        with contextlib.suppress(Exception):
            subprocess.run(
                ["docker", "run", "--rm", "-v", f"{workspace_dir}:/w", "alpine", "sh", "-c",
                 "rm -rf /w/..?* /w/.[!.]* /w/* 2>/dev/null; true"],
                capture_output=True, timeout=300,
            )
        with contextlib.suppress(Exception):
            shutil.rmtree(workspace_dir, ignore_errors=True)
    if image_ref_str:
        with contextlib.suppress(Exception):
            subprocess.run(["docker", "rmi", "-f", image_ref_str], capture_output=True, timeout=180)


# --------------------------------------------------------------------------- #
# Workspace seeding (from /app in the image)
# --------------------------------------------------------------------------- #
def seed_workspace(image_ref_str: str, base_commit: str, destination: str) -> None:
    """Copy the image's /app into destination at base_commit, the way the grader starts.

    Done by a throwaway root container so modes/ownership copy exactly, then handed to the
    container uid so the agent can edit it. A pre-existing dir is emptied first (root-owned).

    The setup mirrors the official grader's tree — ``git reset --hard`` then ``git checkout``
    at base_commit, and *no* ``git clean``. It additionally removes every ref except a fresh
    benchmark-base branch and prunes unreachable objects. Some published instance images
    retain later upstream commits, including the gold fix; leaving those objects addressable
    lets an agent recover answer metadata or the complete patch with ``git show``. Keeping the
    base commit and its ancestors preserves normal repository history without exposing future
    commits. Remotes are removed as a second line of defence behind the network policy.

    ``git clean -fdx`` is deliberately avoided: these images carry a baked-in index quirk
    where a tracked, present file (e.g. ``test/files/toobig.jpg``) reads as deleted, and clean
    would wrongly remove it. Any binary noise that still lands in the diff is dropped by
    ``strip_binary_hunks`` at grade time, again matching the official harness
    (swe_bench_pro_eval.py: assemble_workspace_files).
    """
    os.makedirs(destination, exist_ok=True)
    if not base_commit:
        raise ValueError("base_commit is required to seed a GT-safe SWE-bench Pro workspace")
    quoted_base = shlex.quote(base_commit)
    reset = (
        f"git reset --hard {quoted_base} && "
        f"git checkout -B benchmark-base {quoted_base} && "
        "git remote | while IFS= read -r remote; do git remote remove \"$remote\"; done && "
        "git for-each-ref --format='%(refname)' | while IFS= read -r ref; do "
        "[ \"$ref\" = refs/heads/benchmark-base ] || git update-ref -d \"$ref\"; done && "
        "git reflog expire --expire=now --all && git gc --prune=now; "
    )
    script = (
        'rm -rf /seed/..?* /seed/.[!.]* /seed/* 2>/dev/null; '
        'cp -a /testbed/. /seed/ && cd /seed && '
        f'git config --global --add safe.directory /seed; {reset}'
        'chown -R 1000:1000 /seed'
    )
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh",
         "-v", f"{destination}:/seed", image_ref_str, "-c", script],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"could not seed the workspace from {image_ref_str}: "
            f"{(result.stderr or result.stdout).strip()[:300]}"
        )


async def seed_workspace_async(
    image_ref_str: str, base_commit: str, destination: str,
) -> None:
    """Seed without blocking concurrent agents on the launcher's event loop."""
    await asyncio.to_thread(seed_workspace, image_ref_str, base_commit, destination)


# --------------------------------------------------------------------------- #
# Inner run (inside the container)
# --------------------------------------------------------------------------- #
async def run_inner(args) -> int:
    """Run the MetaAgent on one instance. Called inside the task container."""
    from agentevolver.agent import agent_manager
    from agentevolver.connector import connector_manager
    from agentevolver.extension import extension_manager
    from agentevolver.hook import hook_manager
    from agentevolver.memory import memory_manager
    from agentevolver.model import model_manager
    from agentevolver.prompt import prompt_manager
    from agentevolver.session.context import (
        bind_session_roots,
        configured_session_owner,
        ensure_session_sandbox,
    )
    from agentevolver.session.types import SessionContext
    from agentevolver.skill import skill_manager
    from agentevolver.task import TaskCategory, TaskPriority, TaskStatus, task_manager
    from agentevolver.tool import tool_manager
    from agentevolver.trace import trace_manager
    from agentevolver.trajectory import trajectory_manager
    from agentevolver.version import version_manager

    instance = json.loads(args.instance_json)  # already the SAFE subset (see inner_command)
    instance_id = instance["instance_id"]

    shared_extension_root = config.extension_root
    ctx = SessionContext(id=instance_id, name=f"swebench_verified_{instance_id}")
    fs_sandbox = ensure_session_sandbox(
        ctx,
        owner=configured_session_owner(config),
        shared_extension_root=shared_extension_root,
    )
    bind_session_roots(config, fs_sandbox)
    bind_task_workspace(ctx, fs_sandbox)

    extension_manager.set_base_dir(shared_extension_root)
    logger.initialize(config=config)
    logger.info(f"| 📂 [{instance_id}] Session root: {fs_sandbox.project_root}")
    logger.info(f"| 🧱 [{instance_id}] Working directory: {config.workspace_root}")

    from agentevolver.config import validate_assembly
    for problem in validate_assembly(config):
        logger.warning(f"| ⚠️ Config: {problem}")

    await version_manager.initialize()
    await trace_manager.initialize()
    await trace_manager.start()
    await trajectory_manager.initialize()
    await hook_manager.initialize()
    await model_manager.initialize()
    await prompt_manager.initialize()
    await memory_manager.initialize(memory_names=config.memory_names)
    await tool_manager.initialize(tool_names=config.tool_names)
    await skill_manager.initialize(skill_names=config.skill_names)
    await connector_manager.initialize(connector_names=config.connector_names)
    await agent_manager.initialize(agent_names=config.agent_names)
    manifest = await extension_manager.initialize()
    logger.info(f"| ✅ Extensions: {[f'{c.module}:{c.name}' for c in manifest.components]}")

    task_log_root = str(path_manager.under(config.log_root, P.LOG_MODULE, module="tasks"))
    await task_manager.initialize(log_root=task_log_root, handler=None)
    await task_manager.start(num_workers=1)

    content, task_files, task_meta = build_task_content(instance, args.task_file, task_log_root)

    async def handler(record):
        return await agent_manager(
            name="meta_agent",
            input={"task": record.task.content, "files": record.task.files},
            ctx=ctx,
        )

    task_manager.set_handler(handler)

    started = time.time()
    result: dict = {"instance_id": instance_id, "status": "failed", "error": None}
    try:
        task_id = await task_manager.submit(
            content=content,
            category=TaskCategory.USER,
            priority=TaskPriority.HIGH,
            files=task_files,
            metadata={"instance_id": instance_id, "repository": instance.get("repo", ""),
                      "language": instance.get("repo_language", ""), **task_meta},
            session_id=instance_id,
        )
        while True:
            record = await task_manager.get(task_id)
            if record and record.task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
                break
            await asyncio.sleep(1)

        data = getattr(record.result, "data", None) or {}
        exhausted = bool(data.get("stopped_by_constraint"))
        result.update(
            status="done" if (record.task.status == TaskStatus.DONE or exhausted) else record.task.status.value,
            error=record.error,
            steps=data.get("step"),
            max_step=data.get("max_step"),
            stopped_by_constraint=exhausted,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"| ❌ [{instance_id}] run failed: {e}", exc_info=True)
        result["error"] = str(e)

    # Collect the patch (the diff from base_commit), whatever the status — an unfinished
    # attempt still has edits worth grading.
    try:
        patch = collect_patch(CONTAINER_APP, instance.get("base_commit", ""))
        patch_path = str(path_manager.under(fs_sandbox.project_root, P.PROJECT_PATCH))
        with open(patch_path, "w", encoding="utf-8") as handle:
            handle.write(patch)
        result["patch_path"] = patch_path
        result["patch_bytes"] = len(patch)
        result["has_patch"] = bool(patch.strip())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"| ⚠️ [{instance_id}] could not collect the patch: {e}")
        result["patch_error"] = str(e)

    result["time_seconds"] = round(time.time() - started, 1)
    result["session_path"] = str(fs_sandbox.project_root)
    result["spend"] = _summarise_spend(str(fs_sandbox.project_root), instance_id)

    result_path = path_manager.under(fs_sandbox.project_root, P.PROJECT_RESULT)
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    for label, step in (("task manager", task_manager.stop), ("trace manager", trace_manager.stop)):
        try:
            await step()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️ {label}: {e}")

    logger.info(f"| {'✅' if result['status'] == 'done' else '❌'} [{instance_id}] {result['status']} "
                f"in {result['time_seconds']}s, {result.get('steps')} steps")
    return 0 if result["status"] == "done" else 1


def inner_command(row: dict, args) -> str:
    """The command the launcher runs inside the container — SAFE fields only in the payload."""
    payload = json.dumps(_safe_instance(row))
    parts = [
        shlex.quote(sys.executable),
        shlex.quote(f"{CONTAINER_REPO}/examples/run_swebench_verified.py"),
        "--instance-json", shlex.quote(payload),
        "--config", shlex.quote(args.config.replace(root, CONTAINER_REPO)),
        "--task-file", shlex.quote(args.task_file.replace(root, CONTAINER_REPO)),
    ]
    overrides = getattr(args, "user_cfg_options", None) or {}
    if overrides:
        parts.append("--cfg-options")
        parts.extend(shlex.quote(f"{k}={v}") for k, v in overrides.items())
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Launcher (host) — one container per instance
# --------------------------------------------------------------------------- #
async def run_launcher(args) -> int:
    from agentevolver.sandbox import sandbox_manager
    from agentevolver.visual import BenchmarkMonitor

    check_shared_roots_readable()

    # A benchmark instance may be run repeatedly with different models. Session state
    # includes memory, trajectories, prompts and grader counters, so keying it only by
    # instance_id can leak an earlier model's work into a later run. Give every launcher
    # invocation its own owner namespace unless the caller deliberately supplied one.
    overrides = dict(getattr(args, "user_cfg_options", None) or {})
    if not overrides.get("output_owner"):
        run_namespace = f"{config.get('tag') or 'swebench_verified'}_{time.strftime('%Y%m%d_%H%M%S')}"
        overrides["output_owner"] = run_namespace
        args.user_cfg_options = overrides
        setattr(config, "output_owner", run_namespace)
        logger.info(f"| 🧰 Isolated run namespace: {run_namespace}")


    instances = await load_instances()
    task_ids = [t.strip() for t in args.task_ids.split(",")] if args.task_ids else None
    selected = select_instances(instances, task_ids=task_ids, start=args.start, end=args.end)
    if not selected:
        logger.error("| ❌ no instances selected")
        return 1
    logger.info(f"| 📋 SWE-bench Verified: {len(selected)} instance(s), concurrency={args.concurrency}")

    wall_clock = int(getattr(config, "WALL_CLOCK", 7200) or 7200)
    forwarded = {k: os.environ[k] for k in _FORWARDED_ENV if os.environ.get(k)}
    prefix = interpreter_prefix()
    mounts = {root: CONTAINER_REPO, prefix: prefix}

    # The shared extension library lives inside the mounted repo at
    # ``/AgentEvolver/extension``; the inner run writes its manifest there as the
    # container user. Grant it once here (not per instance — concurrent instances
    # would chown it under one another); ``restore_ownership`` below hands it back.
    grant_to_container_user(str(path_manager.get(P.EXTENSION)))

    results: list = []
    results_lock = asyncio.Lock()
    out_dir = args.out or str(path_manager.under(
        config.project_root,
        P.PROJECT_RESULT_RUN,
        run_id=time.strftime("run_%Y%m%d_%H%M%S"),
    ))
    os.makedirs(out_dir, exist_ok=True)
    results_path = str(path_manager.resolve_under(out_dir, "results.json"))
    owner = str(config.get("output_owner") or config.tag)
    monitor = BenchmarkMonitor(
        out_dir,
        "swebench_verified",
        len(selected),
        args.concurrency,
        results_path=results_path,
        owner_dir=str(path_manager.get(P.OWNER, owner=owner)),
        title="SWE-bench Verified",
    )
    if not args.no_monitor:
        try:
            url = await monitor.deploy(args.monitor_port)
            logger.info(f"| 📊 Benchmark monitor: {url or 'deployment failed'}")
        except Exception as error:
            logger.warning(f"| ⚠️ Benchmark monitor unavailable: {error}")

    def _write_results():
        with open(results_path, "w", encoding="utf-8") as handle:
            json.dump(results, handle, ensure_ascii=False, indent=2)

    async def _run_one_instance(index, row):
        instance_id = row["instance_id"]
        ref = image_ref(row)
        monitor.task(instance_id, "preparing", position=index)
        logger.info(f"| ▶️ [{index}/{len(selected)}] {instance_id} (image={ref})")
        started = time.time()
        record: dict = {"instance_id": instance_id, "status": "failed", "error": None}
        sandbox = None
        workspace_dir = None
        try:
            owner = str(config.get("output_owner") or config.tag)
            workspace_dir = str(path_manager.get(
                P.SESSION_WORKSPACE, owner=owner, session_id=instance_id,
            ))
            session_path = str(path_manager.get(
                P.SESSION, owner=owner, session_id=instance_id,
            ))
            submission_path = os.path.join(session_path, "submission.json")
            if os.path.exists(submission_path):
                submission = load_submission(submission_path, instance_id, row["base_commit"])
                record.update(submission.get("agent_result") or {})
            else:
                await seed_workspace_async(ref, row.get("base_commit", ""), workspace_dir)

                grant_to_container_user(session_path)
                logger.info(f"| 🌱 [{instance_id}] Seeded and mounted at {CONTAINER_APP} (owned by {CONTAINER_USER})")

                sandbox = await sandbox_manager.acquire(
                    "docker", reuse_key=instance_id, image=ref, network=False, user=CONTAINER_USER,
                    workdir=CONTAINER_APP,
                    mounts={**mounts, workspace_dir: CONTAINER_APP},
                    env={
                        "PYTHONPATH": CONTAINER_REPO,
                        "AGENTEVOLVER_HOME": CONTAINER_REPO,
                        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                        **forwarded,
                    },
                    timeout_minutes=(wall_clock + _LANDING_MARGIN_SECONDS) // 60,
                )
                command = inner_command(row, args)
                monitor.task(instance_id, "solving", position=index)
                logger.info(f"| 🚀 [{instance_id}] {command[:160]}")
                execution = await sandbox.run_command(
                    command, workspace_root=CONTAINER_REPO, timeout=wall_clock + _LANDING_MARGIN_SECONDS)
                if execution.stdout:
                    print(execution.stdout)
                if execution.stderr:
                    print(execution.stderr, file=sys.stderr)

                inner_result = str(path_manager.under(session_path, P.PROJECT_RESULT))
                if os.path.isfile(inner_result):
                    with open(inner_result, encoding="utf-8") as handle:
                        record.update(json.load(handle))
                else:
                    record["error"] = f"the inner run left no result.json (container exit {execution.exit_code})"
                record["egress_audit"] = sandbox_manager.egress_audit(sandbox)

                await sandbox_manager.release("docker", reuse_key=instance_id)
                sandbox = None
            submission = freeze_submission(submission_path, workspace_dir, row, record)
            monitor.task(instance_id, "grading", position=index)
            evaluated = await benchmark_manager.eval("swebench_verified", Task(
                task_id=instance_id, result=submission, extra={"workspace_dir": workspace_dir}))
            final = evaluated.evaluation.details
            record.update(evaluation_protocol="swe-final-only-v1", grader_evals=0,
                          submission_sha256=submission["sha256"])
            if final.get("error_code"):
                record["error"] = final["error_code"]
            record["final_grade"] = final
            record["resolved"] = bool(final.get("resolved"))
            logger.info(f"| {'🟢 RESOLVED' if record['resolved'] else '🔴 unresolved'} "
                        f"[{instance_id}] {final}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ [{instance_id}] launcher failure: {e}", exc_info=True)
            record["error"] = str(e)
        finally:
            if sandbox is not None:
                await sandbox_manager.release("docker", reuse_key=instance_id)
            # Bounded disk on a near-full shared volume: each instance has its own ~1GB
            # image and a seeded repo workspace. Reclaim both once graded — result.json and
            # agent.patch live in the session root (not the workspace), so scores survive.
            if getattr(args, "reclaim_disk", True):
                await asyncio.to_thread(_reclaim_instance_disk, workspace_dir, ref)

        record.setdefault("time_seconds", round(time.time() - started, 1))
        logger.info(f"| {'✅' if record['status'] == 'done' else '❌'} [{index}/{len(selected)}] "
                    f"{instance_id} → {record['status']} in {record['time_seconds']}s")
        async with results_lock:
            results.append(record)
            _write_results()
            monitor.finish_task(instance_id)
        return record

    sem = asyncio.Semaphore(max(1, int(args.concurrency or 1)))

    async def _bounded(index, row):
        async with sem:
            try:
                return await _run_one_instance(index, row)
            except Exception as e:  # noqa: BLE001
                logger.error(f"| ❌ [{row['instance_id']}] crashed: {e}")
                record = {"instance_id": row["instance_id"], "status": "failed", "error": str(e)}
                async with results_lock:
                    results.append(record)
                    _write_results()
                    monitor.finish_task(row["instance_id"])
                return record

    completed = False
    try:
        await asyncio.gather(*[_bounded(i + 1, r) for i, r in enumerate(selected)])
        completed = True
    finally:
        if not completed:
            monitor.close("interrupted")
        for shared in (str(path_manager.get(P.OUTPUT)), config.extension_root):
            with contextlib.suppress(Exception):
                restore_ownership(shared)

    done = sum(1 for r in results if r.get("status") == "done")
    monitor.close("completed")
    logger.info(f"| ✅ SWE-bench Pro run done: {done}/{len(selected)} completed. Results: {out_dir}")
    return 0


# --------------------------------------------------------------------------- #
# Args + main
# --------------------------------------------------------------------------- #
def parse_args():
    parser = argparse.ArgumentParser(description="Run AgentEvolver on SWE-bench Pro.")
    parser.add_argument("--instance-json", default=None,
                        help="Inner mode: the SAFE instance subset as JSON. Omit for launcher mode.")
    parser.add_argument("--task-ids", default=None, help="Comma-separated instance_ids.")
    parser.add_argument("--start", type=int, default=None, help="Start index (inclusive).")
    parser.add_argument("--end", type=int, default=None, help="End index (exclusive).")
    parser.add_argument("--config", default=os.path.join(root, "configs", "swebench_pro_agent.py"),
                        help="Config file (evolution arm by default; use ..._baseline.py for the control).")
    parser.add_argument("--task-file", default=DEFAULT_TASK_FILE)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--reclaim-disk", dest="reclaim_disk", action="store_true", default=True,
                        help="After each instance is graded, delete its seeded workspace and its "
                             "docker image to bound disk on a full volume (default on).")
    parser.add_argument("--no-reclaim-disk", dest="reclaim_disk", action="store_false",
                        help="Keep each instance's workspace and image (needs ~1GB per instance).")
    parser.add_argument("--out", default=None, help="Results output directory.")
    parser.add_argument("--no-monitor", action="store_true",
                        help="Do not deploy the live benchmark monitor (enabled by default).")
    parser.add_argument("--monitor-port", type=int, default=8765,
                        help="Preferred monitor port; deploy allocates another when busy.")
    parser.add_argument("--cfg-options", nargs="+", default=None,
                        help="Config overrides, e.g. model_name=llm_hub/claude-opus-5")
    return parser.parse_args()


def parse_cfg_options(items) -> dict[str, str]:
    """Normalize repeatable ``key=value`` CLI overrides before config loading."""
    return dict(item.split("=", 1) for item in (items or []) if "=" in item)


async def main() -> int:
    args = parse_args()
    overrides = parse_cfg_options(args.cfg_options)
    # Config.initialize expects a mapping, while argparse collects repeatable options
    # as a list. Normalize before crossing that boundary.
    args.cfg_options = overrides
    config.initialize(config_path=args.config, args=args)
    args.user_cfg_options = overrides
    if overrides:
        for key, value in overrides.items():
            with contextlib.suppress(Exception):
                setattr(config, key, value)

    if args.instance_json:
        return await run_inner(args)
    return await run_launcher(args)


if __name__ == "__main__":
    import signal

    with contextlib.suppress(Exception):
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt("SIGTERM")))
    sys.exit(asyncio.run(main()))
