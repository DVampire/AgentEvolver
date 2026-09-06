"""Run AgentEvolver on SWE-bench Pro — the same launcher shape as run_programbench.py.

A launcher on the host runs one MetaAgent process per instance; repository commands run
in its task container. The final edits are frozen as a git patch and scored with the
SWE Pro grading scripts. Two arms
(configs/swebench_pro_agent.py and .._baseline.py) differ only in the evolution roster.

Key differences from ProgramBench, all deliberate:

- **The repo lives at ``/app``** in the instance image (``WORKDIR /app; git clone; git
  checkout <base_commit>``). The session workspace is seeded from that ``/app`` on the
  host, then mounted into the agent's container at ``/workspace`` — the framework's
  own workspace root, where the bash tool defaults its cwd. The agent works
  at ``/workspace`` and the patch is ``git diff`` there against the base commit. Only the
  grader's throwaway peer sandbox uses the image-native ``/app`` (see ``_build_entryscript``).

- **GT-SAFETY by field filtering.** The HuggingFace row carries the answer key —
  ``patch`` (gold fix), ``test_patch``, ``fail_to_pass``, ``pass_to_pass``,
  ``selected_test_files_to_run``. Only ``problem_statement`` / ``requirements`` /
  ``interface`` (plus repo/base_commit for setup) are ever handed to the container;
  ``_safe_instance`` strips the rest. The oracle stays on the host, where the grader runs.

- **Final-only grading.** The agent verifies locally. After its process and task sandbox
  stop, the host freezes one patch and grades it in a throwaway peer sandbox. No scores
  return to the agent. Resume after a grader failure reuses that exact frozen submission.

- **Grader assets** — the per-instance ``run_script.sh`` / ``parser.py`` and the dockerfiles
  (for their ENV) live in the cloned checkout (``--grader-repo``, default
  ``others/SWE-bench_Pro``); the entryscript mirrors the official ``create_entryscript``.

Modes: ``launcher`` (default, host) selects instances and owns final grading; ``inner``
(``--instance-json``) is the host-side Agent worker bound to its task container.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shlex
import signal
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
from agentevolver.utils.file_utils import atomic_json_update
from agentevolver.trace.stats import summarise_session_spend

LANDING_MARGIN_SECONDS = 1800
CONTAINER_REPO = "/AgentEvolver"


root = os.environ.get("AGENTEVOLVER_HOST_ROOT") or str(Path(__file__).resolve().parents[1])

DEFAULT_TASK_FILE = os.path.join(root, "examples", "tasks", "swebench_pro_resolution.html")
DEFAULT_GRADER_REPO = os.path.join(root, "others", "SWE-bench_Pro")

#: Seeding copies a whole checkout out of the image and then garbage-collects its history.
#: 900s was not enough for the largest repos in the set.

# The model/runtime stays in the known ``agentos`` host environment. Only repository
# commands execute in the task image, so an Alpine/musl image never has to load a
# glibc-linked host Python.
_EXEC_CONTAINER_ENV = "AGENTEVOLVER_EXEC_CONTAINER"
_EXEC_WORKDIR_ENV = "AGENTEVOLVER_EXEC_WORKDIR"
_TASK_WORKSPACE_ENV = "AGENTEVOLVER_TASK_WORKSPACE"

#: The ONLY instance fields that may enter the container. Everything else in the row is the
#: answer key or host-side scoring metadata and is withheld. Order fixes the task-doc slots.
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
    """Load the SWE-bench Pro split through the public task iterator (no oracle fields).

    Awaited rather than wrapping `asyncio.run`: the only caller is `run_launcher`, which
    is already inside the loop `main()` opened, and a nested `asyncio.run` raises there.
    """
    await benchmark_manager.configure(
        "swebench_pro",
        base_dir=base_dir or "output/benchmark/swebench_pro",
        resume=resume,
        history_path=history_path,
    )
    instances = []
    task = await benchmark_manager.reset("swebench_pro", resume=resume)
    while task is not None:
        instances.append(dict(task.extra or {}))
        task = await benchmark_manager.step("swebench_pro")
    return instances


def _safe_instance(row: dict) -> dict:
    """The subset of a row the container may see — never the gold patch or the tests."""
    return benchmark_manager.task_payload("swebench_pro", row)


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
        "{language}": str(safe.get("repo_language", "")),
        "{problem_statement}": str(safe.get("problem_statement", "")),
        "{requirements}": str(safe.get("requirements", "") or "(none given)"),
        "{interface}": str(safe.get("interface", "") or "(none given)"),
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


def load_run_results(results_path: str) -> list[dict]:
    """Load the durable per-instance ledger used by ``--resume``."""
    if not os.path.isfile(results_path):
        return []
    with open(results_path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"invalid SWE-bench Pro results ledger: {results_path}")
    return value


def solver_environment(env: dict) -> dict:
    """Do not inherit an evaluator bridge from a parent shell or another benchmark."""
    return {
        key: value
        for key, value in env.items()
        if key not in {"AGENTEVOLVER_EVAL_BRIDGE", "AGENTEVOLVER_EVAL_BUDGET"}
    }


def isolated_extensions(owner: str, *, resume: bool) -> str:
    path = str(path_manager.under(path_manager.get(P.OWNER, owner=owner), P.PROJECT_EXTENSION))
    if not resume and os.path.isdir(path) and os.listdir(path):
        raise ValueError("fresh experiment requires an empty run extension directory")
    return path


# --------------------------------------------------------------------------- #
# Inner run (inside the container)
# --------------------------------------------------------------------------- #
async def run_inner(args) -> int:
    """Run the MetaAgent on the host with repository tools bound to its task container."""
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
    for key in ("AGENTEVOLVER_EVAL_BRIDGE", "AGENTEVOLVER_EVAL_BUDGET"):
        os.environ.pop(key, None)
    if "swebench_pro_eval_tool" in config.tool_names:
        raise ValueError("SWE Pro requires local verification only; remove swebench_pro_eval_tool")

    shared_extension_root = config.extension_root
    ctx = SessionContext(id=instance_id, name=f"swebench_pro_{instance_id}")
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

    if getattr(args, "resume", False):
        content = (
            "## RESUMING an interrupted attempt — preserve useful work\n\n"
            "The repository may contain source edits and commits from an earlier process. "
            "Inspect `git status`, `git log`, and the current diff before changing anything. "
            "Continue from useful work, repair incomplete edits, run the repository tests, "
            "and do not reset or rewrite the solution merely because this is a new process.\n\n"
            "The original task contract follows.\n\n---\n\n" + content
        )

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


def inner_argv(row: dict, args, resume: bool = False) -> list[str]:
    """Host-runtime argv for one isolated Agent process — SAFE row fields only."""
    payload = json.dumps(_safe_instance(row))
    parts = [
        sys.executable,
        os.path.join(root, "examples", "run_swebench_pro.py"),
        "--instance-json",
        payload,
        "--config",
        os.path.abspath(args.config),
        "--task-file",
        os.path.abspath(args.task_file),
    ]
    if resume:
        parts.append("--resume")
    overrides = getattr(args, "user_cfg_options", None) or {}
    if overrides:
        parts.append("--cfg-options")
        parts.extend(f"{key}={value}" for key, value in overrides.items())
    return parts


def inner_command(row: dict, args, resume: bool = False) -> str:
    """Printable form of :func:`inner_argv`; never executed through a host shell."""
    return shlex.join(inner_argv(row, args, resume=resume))


async def run_inner_process(row: dict, args, env: dict, resume: bool, timeout: int) -> int:
    """Run one Agent brain in its own host process and bound its whole process group."""
    process = await asyncio.create_subprocess_exec(
        *inner_argv(row, args, resume=resume),
        env=env,
        start_new_session=True,
    )

    async def stop() -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        await stop()
        return 124
    except asyncio.CancelledError:
        await stop()
        raise


# --------------------------------------------------------------------------- #
# Launcher (host) — one container per instance
# --------------------------------------------------------------------------- #
async def run_launcher(args) -> int:
    from agentevolver.visual import BenchmarkMonitor

    if getattr(args, "resume", False) and not args.out:
        logger.error("| ❌ --resume requires a stable --out directory")
        return 1

    out_dir = args.out or str(
        path_manager.under(
            config.project_root,
            P.PROJECT_RESULT_RUN,
            run_id=time.strftime("run_%Y%m%d_%H%M%S"),
        )
    )
    os.makedirs(out_dir, exist_ok=True)
    state_path = path_manager.resolve_under(out_dir, "run_state.json")
    results_path = path_manager.resolve_under(out_dir, "results.json")

    prior_state = {}
    if getattr(args, "resume", False) and os.path.isfile(state_path):
        with open(state_path, encoding="utf-8") as handle:
            prior_state = json.load(handle)
    if not args.resume and (os.path.exists(state_path) or os.path.exists(results_path)):
        raise ValueError("existing run is preserved; use --resume or a new output directory")

    # A benchmark instance may be run repeatedly with different models. Session state
    # includes memory, trajectories, prompts and grader counters, so keying it only by
    # instance_id can leak an earlier model's work into a later run. A fresh launcher gets
    # an isolated owner; a resumed launcher recovers the exact owner from run_state.json.
    overrides = dict(getattr(args, "user_cfg_options", None) or {})
    prior_owner = prior_state.get("output_owner")
    requested_owner = overrides.get("output_owner")
    if prior_owner and requested_owner and prior_owner != requested_owner:
        logger.error(
            f"| ❌ resume owner mismatch: state={prior_owner}, requested={requested_owner}"
        )
        return 1
    if prior_owner:
        overrides["output_owner"] = prior_owner
        args.user_cfg_options = overrides
        setattr(config, "output_owner", prior_owner)
        logger.info(f"| ⏩ Restored run namespace: {prior_owner}")
    elif not requested_owner:
        run_namespace = f"{config.get('tag') or 'swebench_pro'}_{time.strftime('%Y%m%d_%H%M%S')}"
        overrides["output_owner"] = run_namespace
        args.user_cfg_options = overrides
        setattr(config, "output_owner", run_namespace)
        logger.info(f"| 🧰 Isolated run namespace: {run_namespace}")

    owner = str(config.get("output_owner") or config.tag)
    extension_root = isolated_extensions(owner, resume=bool(prior_state))
    overrides["extension_root"] = extension_root
    args.user_cfg_options = overrides
    config.extension_root = extension_root
    os.environ["AGENTEVOLVER_EXTENSION_ROOT"] = extension_root
    grader_repo = os.path.abspath(args.grader_repo)
    try:
        benchmark_info = await benchmark_manager.get_info(
            "swebench_pro",
            evaluation_options={
                "grader_repo": grader_repo,
                "grader_profile": getattr(args, "grader_profile", "official"),
            },
            expected_evaluation=prior_state or None,
        )
    except ValueError as error:
        logger.error(f"| ❌ {error}")
        return 1

    run_state = {
        **benchmark_info.evaluation,
        "extension_root": extension_root,
        "config": os.path.abspath(args.config),
        "task_ids": args.task_ids,
        "start": args.start,
        "end": args.end,
        "output_owner": str(config.get("output_owner") or config.tag),
    }
    if prior_state:
        for key in ("config", "task_ids", "start", "end", "extension_root"):
            if prior_state.get(key) != run_state.get(key):
                logger.error(
                    f"| ❌ resume selection mismatch for {key}: "
                    f"state={prior_state.get(key)!r}, requested={run_state.get(key)!r}"
                )
                return 1
    if not prior_state:
        sessions = path_manager.resolve_under(
            path_manager.get(P.OWNER, owner=run_state["output_owner"]), "sessions"
        )
        if os.path.isdir(sessions) and os.listdir(sessions):
            raise ValueError(
                "output_owner already contains sessions; use a new owner for a fresh experiment"
            )
    atomic_json_update(state_path, lambda _previous: run_state)

    instances = await load_instances(
        base_dir=os.path.join(out_dir, "benchmark"),
        resume=args.resume,
        history_path=str(results_path),
    )
    task_ids = [t.strip() for t in args.task_ids.split(",")] if args.task_ids else None
    selected = select_instances(instances, task_ids=task_ids, start=args.start, end=args.end)
    if not selected:
        logger.error("| ❌ no instances selected")
        return 1
    selection_size = len(selected)

    results = load_run_results(results_path) if getattr(args, "resume", False) else []
    previous_records = {record.get("instance_id"): record for record in results}
    completed_ids = set((await benchmark_manager.stats("swebench_pro")).extra["completed_task_ids"])
    pending = [
        (index, row)
        for index, row in enumerate(selected, start=1)
        if row["instance_id"] not in completed_ids
    ]
    logger.info(
        f"| 📋 SWE-bench Pro: {selection_size} selected, {len(completed_ids)} already "
        f"scored, {len(pending)} pending, concurrency={args.concurrency}"
    )
    owner = str(config.get("output_owner") or config.tag)
    monitor = BenchmarkMonitor(
        out_dir,
        "swebench_pro",
        selection_size,
        args.concurrency,
        results_path=results_path,
        owner_dir=str(path_manager.get(P.OWNER, owner=owner)),
        title="SWE-bench Pro",
    )
    if not args.no_monitor:
        try:
            url = await monitor.deploy(args.monitor_port)
            logger.info(f"| 📊 Benchmark monitor: {url or 'deployment failed'}")
        except Exception as error:  # monitoring must never invalidate a benchmark
            logger.warning(f"| ⚠️ Benchmark monitor unavailable: {error}")
    if not pending:
        monitor.close()
        logger.info(f"| ✅ Nothing to resume; all {selection_size} instances are scored")
        return 0

    wall_clock = int(getattr(config, "WALL_CLOCK", 7200) or 7200)
    forwarded = {k: os.environ[k] for k in _FORWARDED_ENV if os.environ.get(k)}

    # The Agent brain now stays in the known host runtime; only repository commands run
    # inside the task image. Recover the shared library from a previously interrupted
    # container-hosted run before any host worker tries to update its manifest.

    results_lock = asyncio.Lock()
    provision_sem = asyncio.Semaphore(max(1, int(args.provision_concurrency or 1)))

    def _write_results():
        order = {row["instance_id"]: index for index, row in enumerate(selected)}
        results.sort(key=lambda item: order.get(item.get("instance_id"), selection_size))
        atomic_json_update(results_path, lambda _previous: results)

    async def _run_one_instance(index, row):
        instance_id = row["instance_id"]
        previous = previous_records.get(instance_id, {})
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
                "agent_on_host": True,
            }
            async with provision_sem:
                prepared = await benchmark_manager.prepare("swebench_pro", task, context=options)
            if prepared.completed:
                saved = (await benchmark_manager.stats("swebench_pro")).extra["evaluations"][
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
                inner_env = solver_environment(
                    {
                        **os.environ,
                        **forwarded,
                        _EXEC_CONTAINER_ENV: prepared.container_name,
                        _EXEC_WORKDIR_ENV: prepared.container_workspace,
                        _TASK_WORKSPACE_ENV: prepared.agent_workspace,
                        "AGENTEVOLVER_HOME": root,
                        "PYTHONPATH": root,
                        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
                    }
                )
                exit_code = await run_inner_process(
                    row,
                    args,
                    inner_env,
                    resume=prepared.resumed,
                    timeout=wall_clock + LANDING_MARGIN_SECONDS,
                )
                inner_result = str(path_manager.under(session_path, P.PROJECT_RESULT))
                if os.path.isfile(inner_result):
                    with open(inner_result, encoding="utf-8") as handle:
                        record.update(json.load(handle))
                else:
                    record["error"] = f"inner worker left no result.json (exit {exit_code})"
            task = await benchmark_manager.submit("swebench_pro", task, output=record)
            submission = task.result
            record.update(submission.get("agent_result") or {})
            task.time = time.time() - started
            task.extra.update(
                {
                    "workspace_dir": workspace_dir,
                    "grader_repo": grader_repo,
                    "grader_profile": run_state["grader_profile"],
                }
            )
            monitor.task(instance_id, "grading", position=index)
            evaluated = await benchmark_manager.eval("swebench_pro", task)
            record.update(
                final_grade=evaluated.evaluation.details,
                resolved=evaluated.evaluation.status == "passed",
                grader_evals=0,
                submission_sha256=submission["sha256"],
            )
            record.update(benchmark_info.evaluation)
            if evaluated.evaluation.status == "error":
                record["error"] = evaluated.evaluation.details.get("error_code")
        except Exception as error:
            record["error"] = str(error)
            logger.error(f"| ❌ [{instance_id}] {error}", exc_info=True)
        finally:
            await benchmark_manager.cleanup(
                "swebench_pro", task, reclaim=getattr(args, "reclaim_disk", False)
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
                    results[:] = [
                        item for item in results if item.get("instance_id") != row["instance_id"]
                    ]
                    results.append(record)
                    _write_results()
                    monitor.finish_task(row["instance_id"])
                return record

    try:
        await asyncio.gather(*[_bounded(i, row) for i, row in pending])
    except BaseException:
        monitor.close("interrupted")
        raise
    finally:
        summary = await benchmark_manager.stats("swebench_pro")
        await benchmark_manager.cleanup("swebench_pro")

    # Two different numbers, and reporting only the first is how a run that never happened
    # reads as a run that did. `status == "done"` says the agent finished its turn;
    # `resolved` says the grader accepted the patch. An instance that never produced a score
    # — a seeding timeout, a crash — is a harness failure, not a benchmark answer, so it
    # leaves through a non-zero exit while an honestly unresolved instance does not.
    done = sum(1 for r in results if r.get("status") == "done")
    resolved = summary.correct
    scored = [
        r
        for r in results
        if isinstance(r.get("final_grade"), dict) and not r["final_grade"].get("error_code")
    ]
    unscored = [r for r in results if r not in scored]
    logger.info(
        f"| ✅ SWE-bench Pro: {resolved}/{selection_size} resolved, "
        f"{len(scored)}/{selection_size} scored, {done}/{selection_size} ran. Results: {out_dir}"
    )
    for r in unscored:
        grade = r.get("final_grade") or {}
        logger.error(
            f"| ❌ {r.get('instance_id')} produced no score: "
            f"{grade.get('error_code') or r.get('error') or 'unknown'}"
        )
    monitor.close("completed" if not unscored else "completed_with_errors")
    return 1 if unscored else 0


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
    parser.add_argument(
        "--grader-repo",
        default=DEFAULT_GRADER_REPO,
        help="Path to the cloned scaleapi/SWE-bench_Pro-os checkout.",
    )
    parser.add_argument(
        "--grader-profile",
        choices=("official", "diagnostic"),
        default="official",
        help="Official uses unchanged upstream scripts; diagnostic enables custom repairs and is non-comparable.",
    )
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--provision-concurrency",
        type=int,
        default=4,
        help="Maximum concurrent image pulls/workspace seeds, separate from Agent workers.",
    )
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
        "--resume",
        action="store_true",
        help="Resume a durable run from --out: restore its output namespace, skip every "
        "instance with a recorded final score, regrade frozen submissions without "
        "restarting the Agent, or reuse a pre-submission interrupted workspace. "
        "Resolved and unresolved scores are both terminal.",
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
    if args.provision_concurrency < 1:
        parser.error("--provision-concurrency must be positive")
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
    # as a list. It also mutates that mapping with ordinary launcher arguments, so keep a
    # separate copy: forwarding start/end/out host paths into the inner config previously
    # polluted every container invocation.
    args.cfg_options = dict(overrides)
    config.initialize(config_path=args.config, args=args)
    args.user_cfg_options = dict(overrides)
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
