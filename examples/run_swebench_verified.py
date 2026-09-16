"""Run SWE-bench Verified tasks and submit frozen patches to BenchmarkManager.

The launcher owns task scheduling and agent lifecycle. The benchmark manager
owns the host-side official harness; hidden grading never returns to an agent.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentevolver.benchmark import benchmark_manager
from agentevolver.benchmark.types import Task
from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.trace.stats import summarise_session_spend

LANDING_MARGIN_SECONDS = 1800
CONTAINER_REPO = "/AgentEvolver"


root = os.environ.get("AGENTEVOLVER_HOST_ROOT") or str(Path(__file__).resolve().parents[1])

DEFAULT_TASK_FILE = os.path.join(root, "examples", "tasks", "swebench_verified_resolution.html")

#: The ONLY instance fields that may enter the container. Everything else in the row is the
#: answer key or host-side scoring metadata and is withheld. Order fixes the task-doc slots.
#: SWE-bench Verified carries no requirements/interface — problem_statement is the whole spec.
#: Solver-visible fields are selected by benchmark_manager.task_payload().

_FORWARDED_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_BASE",
    "GOOGLE_API_KEY",
    "GOOGLE_API_BASE",
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_BASE",
    "LLM_HUB_API_KEY",
    "LLM_HUB_API_BASE",
)

# --------------------------------------------------------------------------- #
# Dataset + GT-safe field filtering
# --------------------------------------------------------------------------- #


async def load_instances(*, base_dir=None, resume=False, history_path=None) -> list:
    """Load the SWE-bench Verified split through the public task iterator (no oracle fields)."""

    await benchmark_manager.configure(
        "swebench_verified",
        base_dir=base_dir or "output/benchmark/swebench_verified",
        resume=resume,
        history_path=history_path,
    )
    instances = []
    task = await benchmark_manager.reset("swebench_verified", resume=resume)
    while task is not None:
        instances.append(dict(task.extra or {}))
        task = await benchmark_manager.step("swebench_verified")
    return instances


def _safe_instance(row: dict) -> dict:
    """The subset of a row the container may see — never the gold patch or the tests."""
    return benchmark_manager.task_payload("swebench_verified", row)


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

    ``instance_full`` may be the full row; only manager-approved solver fields are read from it, so the
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
        view_path = str(
            path_manager.under(
                task_log_root,
                P.LOG_TASK_VIEW,
                filename=f"task_view_{safe.get('instance_id', 'task')}.html",
            )
        )
        os.makedirs(task_log_root, exist_ok=True)
        render_task_page(html_body, view_path, title=doc.title)
        metadata["task_view"] = view_path

    return content, [doc.source_path], metadata


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
    task_workspace = os.environ["AGENTEVOLVER_TASK_WORKSPACE"]
    config.workspace_root = task_workspace
    path_manager.override(P.SESSION_WORKSPACE, task_workspace)

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
            metadata={
                "instance_id": instance_id,
                "repository": instance.get("repo", ""),
                "language": instance.get("repo_language", ""),
                **task_meta,
            },
            session_id=instance_id,
        )
        while True:
            record = await task_manager.get(task_id)
            if record and record.task.status in (
                TaskStatus.DONE,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                break
            await asyncio.sleep(1)

        data = getattr(record.result, "data", None) or {}
        exhausted = bool(data.get("stopped_by_constraint"))
        result.update(
            status="done"
            if (record.task.status == TaskStatus.DONE or exhausted)
            else record.task.status.value,
            error=record.error,
            steps=data.get("step"),
            max_step=data.get("max_step"),
            stopped_by_constraint=exhausted,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"| ❌ [{instance_id}] run failed: {e}", exc_info=True)
        result["error"] = str(e)

    result["time_seconds"] = round(time.time() - started, 1)
    result["session_path"] = str(fs_sandbox.project_root)
    result["spend"] = summarise_session_spend(str(fs_sandbox.project_root), instance_id)

    result_path = path_manager.under(fs_sandbox.project_root, P.PROJECT_RESULT)
    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    for label, step in (("task manager", task_manager.stop), ("trace manager", trace_manager.stop)):
        try:
            await step()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️ {label}: {e}")

    logger.info(
        f"| {'✅' if result['status'] == 'done' else '❌'} [{instance_id}] {result['status']} "
        f"in {result['time_seconds']}s, {result.get('steps')} steps"
    )
    return 0 if result["status"] == "done" else 1


def inner_command(row: dict, args) -> str:
    """The command the launcher runs inside the container — SAFE fields only in the payload."""
    payload = json.dumps(_safe_instance(row))
    parts = [
        shlex.quote(sys.executable),
        shlex.quote(f"{CONTAINER_REPO}/examples/run_swebench_verified.py"),
        "--instance-json",
        shlex.quote(payload),
        "--config",
        shlex.quote(args.config.replace(root, CONTAINER_REPO)),
        "--task-file",
        shlex.quote(args.task_file.replace(root, CONTAINER_REPO)),
    ]
    overrides = getattr(args, "user_cfg_options", None) or {}
    if overrides:
        parts.append("--cfg-options")
        parts.extend(shlex.quote(f"{k}={v}") for k, v in overrides.items())
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Launcher (host) — one container per instance
# --------------------------------------------------------------------------- #
async def run_worker_command(command, container_name, timeout):
    import signal

    process = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        container_name,
        "sh",
        "-lc",
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def run_launcher(args) -> int:
    from agentevolver.visual import BenchmarkMonitor

    # A benchmark instance may be run repeatedly with different models. Session state
    # includes memory, trajectories, prompts and grader counters, so keying it only by
    # instance_id can leak an earlier model's work into a later run. Give every launcher
    # invocation its own owner namespace unless the caller deliberately supplied one.
    overrides = dict(getattr(args, "user_cfg_options", None) or {})
    if args.resume and not overrides.get("output_owner"):
        raise ValueError("--resume requires the original output_owner in --cfg-options")
    if not overrides.get("output_owner"):
        run_namespace = (
            f"{config.get('tag') or 'swebench_verified'}_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        overrides["output_owner"] = run_namespace
        args.user_cfg_options = overrides
        setattr(config, "output_owner", run_namespace)
        logger.info(f"| 🧰 Isolated run namespace: {run_namespace}")

    instances = await load_instances(
        base_dir=str(
            path_manager.get(P.OWNER, owner=str(config.get("output_owner") or config.tag))
            / "benchmark"
        ),
        resume=args.resume,
    )
    task_ids = [t.strip() for t in args.task_ids.split(",")] if args.task_ids else None
    selected = select_instances(instances, task_ids=task_ids, start=args.start, end=args.end)
    if not selected:
        logger.error("| ❌ no instances selected")
        return 1
    logger.info(
        f"| 📋 SWE-bench Verified: {len(selected)} instance(s), concurrency={args.concurrency}"
    )

    wall_clock = int(getattr(config, "WALL_CLOCK", 7200) or 7200)
    forwarded = {k: os.environ[k] for k in _FORWARDED_ENV if os.environ.get(k)}
    prefix = sys.base_prefix if sys.base_prefix != sys.prefix else sys.prefix
    mounts = {root: CONTAINER_REPO, prefix: prefix}

    # The shared extension library lives inside the mounted repo at
    # ``/AgentEvolver/extension``; the inner run writes its manifest there as the
    # container user. Grant it once here (not per instance — concurrent instances
    # would chown it under one another); ``restore_ownership`` below hands it back.

    results: list = []
    results_lock = asyncio.Lock()
    out_dir = args.out or str(
        path_manager.under(
            config.project_root,
            P.PROJECT_RESULT_RUN,
            run_id=time.strftime("run_%Y%m%d_%H%M%S"),
        )
    )
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
        previous = {}
        record = {"instance_id": instance_id, "status": "failed", "error": None, **previous}
        task = Task(task_id=instance_id, extra=dict(row))
        started = time.time()
        prepared = None
        monitor.task(instance_id, "preparing", position=index)
        try:
            owner = str(config.get("output_owner") or config.tag)
            session_path = str(path_manager.get(P.SESSION, owner=owner, session_id=instance_id))
            workspace_dir = str(
                path_manager.get(P.SESSION_WORKSPACE, owner=owner, session_id=instance_id)
            )
            options = {
                "session_dir": session_path,
                "workspace_dir": workspace_dir,
                "output_dir": out_dir,
                "resume": bool(args.resume),
                "previous": previous,
                "timeout": wall_clock + LANDING_MARGIN_SECONDS,
                "agent_on_host": False,
                "mounts": mounts,
                "writable_paths": [config.extension_root],
                "env": {
                    "PYTHONPATH": CONTAINER_REPO,
                    "AGENTEVOLVER_HOME": CONTAINER_REPO,
                    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                    **forwarded,
                },
            }
            prepared = await benchmark_manager.prepare("swebench_verified", task, context=options)
            if prepared.completed:
                saved = (await benchmark_manager.stats("swebench_verified")).extra["evaluations"][
                    instance_id
                ]
                saved_result = saved.get("result") or {}
                record.update(saved_result.get("agent_result") or {"status": "historical"})
                record.update(
                    final_grade=saved["evaluation"]["details"], resolved=saved["score"] == 1
                )
                return record
            if prepared.submission is None:
                monitor.task(instance_id, "solving", position=index)
                execution = await run_worker_command(
                    inner_command(row, args),
                    prepared.container_name,
                    wall_clock + LANDING_MARGIN_SECONDS,
                )
                exit_code = execution.returncode
                if execution.stdout:
                    print(execution.stdout)
                if execution.stderr:
                    print(execution.stderr, file=sys.stderr)
                inner_result = str(path_manager.under(session_path, P.PROJECT_RESULT))
                if os.path.isfile(inner_result):
                    with open(inner_result, encoding="utf-8") as handle:
                        record.update(json.load(handle))
                else:
                    record["error"] = f"inner worker left no result.json (exit {exit_code})"
            task = await benchmark_manager.submit("swebench_verified", task, output=record)
            submission = task.result
            record.update(submission.get("agent_result") or {})
            task.time = time.time() - started
            task.extra.update({"workspace_dir": workspace_dir})
            monitor.task(instance_id, "grading", position=index)
            evaluated = await benchmark_manager.eval("swebench_verified", task)
            record.update(
                final_grade=evaluated.evaluation.details,
                resolved=evaluated.evaluation.status == "passed",
                grader_evals=0,
                submission_sha256=submission["sha256"],
            )
            if evaluated.evaluation.status == "error":
                record["error"] = evaluated.evaluation.details.get("error_code")
        except Exception as error:
            record["error"] = str(error)
            logger.error(f"| ❌ [{instance_id}] {error}", exc_info=True)
        finally:
            await benchmark_manager.cleanup(
                "swebench_verified", task, reclaim=getattr(args, "reclaim_disk", False)
            )
            record.setdefault("time_seconds", round(time.time() - started, 1))
            async with results_lock:
                results[:] = [item for item in results if item.get("instance_id") != instance_id]
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
        summary = await benchmark_manager.stats("swebench_verified")
        await benchmark_manager.cleanup("swebench_verified")

    done = sum(1 for r in results if r.get("status") == "done")
    monitor.close("completed_with_errors" if summary.errors else "completed")
    logger.info(
        f"| ✅ SWE-bench Pro run done: {done}/{len(selected)} completed. Results: {out_dir}"
    )
    return 1 if summary.errors else 0


# --------------------------------------------------------------------------- #
# Args + main
# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run AgentEvolver on SWE-bench Pro.")
    parser.add_argument(
        "--instance-json",
        default=None,
        help="Inner mode: the SAFE instance subset as JSON. Omit for launcher mode.",
    )
    parser.add_argument("--task-ids", default=None, help="Comma-separated instance_ids.")
    parser.add_argument("--start", type=int, default=None, help="Start index (inclusive).")
    parser.add_argument("--end", type=int, default=None, help="End index (exclusive).")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "swebench_pro_agent.py"),
        help="Config file (evolution arm by default; use ..._baseline.py for the control).",
    )
    parser.add_argument("--task-file", default=DEFAULT_TASK_FILE)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--reclaim-disk",
        dest="reclaim_disk",
        action="store_true",
        default=True,
        help="After each instance is graded, delete its seeded workspace and its "
        "docker image to bound disk on a full volume (default on).",
    )
    parser.add_argument(
        "--no-reclaim-disk",
        dest="reclaim_disk",
        action="store_false",
        help="Keep each instance's workspace and image (needs ~1GB per instance).",
    )
    parser.add_argument(
        "--resume", action="store_true", help="Resume the same output_owner and frozen submissions."
    )
    parser.add_argument("--out", default=None, help="Results output directory.")
    parser.add_argument(
        "--no-monitor",
        action="store_true",
        help="Do not deploy the live benchmark monitor (enabled by default).",
    )
    parser.add_argument(
        "--monitor-port",
        type=int,
        default=8765,
        help="Preferred monitor port; deploy allocates another when busy.",
    )
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        default=None,
        help="Config overrides, e.g. model_name=llm_hub/claude-opus-5",
    )
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    return args


def parse_cfg_options(items) -> dict[str, str]:
    """Normalize repeatable ``key=value`` CLI overrides before config loading."""
    return (
        dict(items)
        if isinstance(items, dict)
        else dict(item.split("=", 1) for item in (items or []) if "=" in item)
    )


async def main(argv=None) -> int:
    args = parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv(os.path.join(root, ".env"))
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

    def _terminate_via_exception(signum, _frame):
        raise KeyboardInterrupt(f"terminated by signal {signum}")

    signal.signal(signal.SIGTERM, _terminate_via_exception)
    sys.exit(asyncio.run(main()))
