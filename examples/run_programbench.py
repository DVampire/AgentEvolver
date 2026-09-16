"""Run MetaAgent on selected ProgramBench tasks.

Runs tasks and submits frozen archives to BenchmarkManager. See
agentevolver/benchmark/default/programbench.py for
the `programbench eval`-based scorer, which can be pointed at this script's output
(submission.tar.gz per task) separately.

Architecture
------------
The agent runs **inside the task's own image**. That is the only arrangement where the
toolchain it probes is the toolchain that will build its code: each instance ships a
~3GB image carrying that project's compiler, headers and reference binary, and no
general-purpose image can carry 201 projects' build dependencies. cmatrix needs
`ncurses.h`; shellharden needs a Rust toolchain; a base image has neither, so an agent
working anywhere else would probe one environment and build in another.

So this script has two modes:

* **launcher** (default, run on the host) — selects instances and, for each, starts a
  container from that instance's image with the framework and its interpreter mounted
  in, then runs the inner mode inside it.
* **inner** (``--instance-json …``, run inside that container) — the actual MAS run.
  There is no sandbox routing: the tools operate on the local filesystem, which *is*
  the task environment, and ``/workspace`` is right there with the docs, the reference
  binary and the git repo.

Because the body of the per-instance loop is a container, the loop lives in the
launcher rather than inside the agent process.

Network
-------
The container is created with no network interface. Its only route out is a Unix socket
belonging to a relay on the host, which permits the model endpoints and nothing else
(``sandbox_allow_model_endpoints`` / ``sandbox_deny_hosts`` in the config). So the
agent's brain can reach a model while its shell cannot reach the internet — the
benchmark's anti-cheat, and load-bearing: runs have been observed attempting `git clone`
and `curl` against the upstream repository. Verified from inside a container:
`git clone https://github.com/…` returns "HTTP code 403 from proxy after CONNECT", and a
raw connect to 1.1.1.1 returns "Network is unreachable". Every attempt is recorded in the
run's results, so "the work was offline" is checkable rather than assumed.

Nothing can be installed inside the container either, so the launcher passes each
instance's metadata in on the command line rather than expecting the benchmark dataset
to be importable there.

Usage
-----
    # Two named instances, self-evolution roster per the default config
    python examples/run_programbench.py --task-ids <instance_id_1>,<instance_id_2>

    # The control arm. A config, not a flag, so a result's roster is readable from the
    # repo instead of reconstructed from a command line.
    python examples/run_programbench.py --start 0 --end 5 \
        --config configs/programbench_agent_baseline.py

    # Override config options
    python examples/run_programbench.py --start 0 --end 1 \
        --cfg-options model_name=openai/o3
"""

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from mmengine import DictAction

root = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, root)

from agentevolver.config import config
from agentevolver.benchmark import benchmark_manager
from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.trace.stats import summarise_session_spend
from agentevolver.sandbox import sandbox_manager
from agentevolver.utils import make_id

LANDING_MARGIN_SECONDS = 1800
CONTAINER_REPO = "/AgentEvolver"


DEFAULT_TASK_FILE = os.path.join(root, "examples", "tasks", "programbench_reconstruction.html")

#: Environment the inner run needs. Nothing else is forwarded into the container.
_FORWARDED_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_API_BASE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_BASE",
    "GOOGLE_API_KEY",
    "GOOGLE_API_BASE",
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_BASE",
)


# --------------------------------------------------------------------------- #
# Instance selection and task construction (shared by both modes)
# --------------------------------------------------------------------------- #
def select_instances(instances, task_ids=None, start=None, end=None):
    """Select a subset of ProgramBench instances by id list and/or index range.

    `task_ids` takes precedence over `start`/`end` (a warning is returned, not
    raised, so a run does not die over a redundant argument).
    """
    warnings = []
    if not task_ids and start is None and end is None:
        raise ValueError("select_instances requires task_ids or start/end")

    if task_ids:
        by_id = {i["instance_id"]: i for i in instances}
        selected = [by_id[tid] for tid in task_ids if tid in by_id]
        if start is not None or end is not None:
            warnings.append("--start/--end ignored because --task-ids was given")
        unknown = [tid for tid in task_ids if tid not in by_id]
        if unknown:
            warnings.append(f"unknown task id(s) skipped: {unknown}")
        return selected, warnings

    return list(instances[start:end]), warnings


def build_task_content(instance, task_file=None, task_log_root=None):
    """Resolve the task document for one ProgramBench instance.

    Returns ``(content, files, metadata)``, matching what ``resolve_task`` hands to
    ``task_manager.submit``. This does not simply call ``resolve_task`` because the
    document carries per-instance placeholders: only the target program varies across
    the 201 instances, and it is substituted into the objective rather than appended
    after it — the agent should know what it is rebuilding before reading five
    thousand characters of method.

    Substitution is a literal replace rather than ``str.format``: the document is
    full of shell and code samples whose braces would otherwise need escaping, and
    an escaping mistake there fails silently.

    Both the agent's text and the rendered view are substituted. Filling only the
    text leaves whoever opens ``task_view.html`` looking at a raw ``{repository}``.
    """
    from agentevolver.task import load_task_document
    from agentevolver.visual import render_task_page

    path = task_file or DEFAULT_TASK_FILE
    doc = load_task_document(path)
    slots = {
        "{repository}": str(instance.get("repository", "")),
        "{language}": str(instance.get("language", "")),
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
                filename=f"task_view_{instance.get('instance_id', 'task')}.html",
            )
        )
        os.makedirs(task_log_root, exist_ok=True)
        render_task_page(html_body, view_path, title=doc.title)
        metadata["task_view"] = view_path

    return content, [doc.source_path], metadata


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run MetaAgent on selected ProgramBench tasks")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "programbench_agent.py"),
        help="Config file path",
    )
    parser.add_argument(
        "--task-file",
        default=DEFAULT_TASK_FILE,
        help="Task document (.html/.md) carrying the reconstruction instructions. "
        "Swap it to try a different prompt without editing this script.",
    )
    parser.add_argument(
        "--task-ids", default=None, help="Comma-separated ProgramBench instance_id list"
    )
    parser.add_argument(
        "--start",
        type=int,
        default=None,
        help="Start index (inclusive) into the loaded instance list",
    )
    parser.add_argument(
        "--end", type=int, default=None, help="End index (exclusive) into the loaded instance list"
    )
    parser.add_argument("--out", default=None, help="Results JSON output directory")
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
        "--concurrency",
        type=int,
        default=1,
        help="How many instances to run at once (each in its own container). Default 1 "
        "(sequential). Every instance is independent — its own container, session, "
        "workspace and final evaluation — so raising this just runs more in parallel; size "
        "it to the host's CPU/RAM and to the extra grader containers each eval spawns.",
    )
    parser.add_argument(
        "--instance-json",
        default=None,
        help="Inner mode: JSON metadata for the single instance to run. Set by the "
        "launcher when it starts this script inside a task container, where the "
        "benchmark dataset is not importable and nothing can be installed, so the "
        "metadata travels on the command line.",
    )
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        action=DictAction,
        help="Override config options in key=value format",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue from a session's existing workspace instead of re-seeding it from "
        "the image. The prior run's committed reconstruction, its build script, and "
        "the preserved reference binary are kept, and the agent is told to build on "
        "them and close the remaining behaviour gaps rather than start over. Only the "
        "agent's own reconstruction carries across — no test suite or reference source "
        "— so it stays within the benchmark's rules; the score then reflects work "
        "accumulated across runs rather than a single fresh attempt.",
    )
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    if not args.instance_json and not args.task_ids and args.start is None and args.end is None:
        parser.error(
            "specify --task-ids or --start/--end; refusing to run the entire benchmark by default"
        )
    return args


# --------------------------------------------------------------------------- #
# Deliverable
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Inner mode — the MAS run for one instance, inside that instance's container
# --------------------------------------------------------------------------- #


async def run_inner(args) -> int:
    """Run the agent on one instance. Called inside the task container."""
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

    instance = json.loads(args.instance_json)
    instance_id = instance["instance_id"]

    shared_extension_root = config.extension_root
    # The session id IS the instance id, so a run lands in
    # output/<owner>/sessions/<instance_id>/ and is findable by task name rather than by
    # an opaque hash. Re-running reuses the directory and overwrites the previous
    # submission — copy anything worth keeping out first.
    ctx = SessionContext(id=instance_id, name=f"programbench_{instance_id}")
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
        # The launcher kept a prior run's workspace instead of re-seeding. A fresh agent
        # reading the task from scratch would try to `mv executable reference_executable`
        # (there is no `executable` now — the reference is already renamed) and might
        # rewrite from zero. Tell it what it is walking into: build on what is here, and
        # spend the run closing the gaps the existing reconstruction still has.
        content = (
            "## RESUMING an earlier attempt — do not start over\n\n"
            "This workspace already holds a substantial reconstruction from a previous run:"
            " your own source, a working `./compile.sh`, and a git history (see `git log`)."
            " The reference binary is ALREADY preserved as `./reference_executable` — do NOT"
            " look for `./executable` to rename, and do NOT rewrite from scratch.\n\n"
            "Your job now is to CONTINUE and CLOSE THE GAPS: build with `./compile.sh`, run"
            " your existing differential checks (and write them if none remain) against"
            " `./reference_executable`, find behaviours that still differ or were never"
            " exercised — every subcommand, both flag forms, env vars, exit codes and"
            " streams, edge and error inputs, interactive/subprocess and file-format"
            " behaviour — fix them, and commit. Use only the reference as the oracle, never"
            " any external source or the hidden tests. The full task contract follows.\n\n"
            "---\n\n" + content
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
                "repository": instance.get("repository", ""),
                "language": instance.get("language", ""),
                "image_name": instance.get("image_name", ""),
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
        # Running out of budget is a *reason*, not a different outcome: the workspace is
        # still there and still worth grading, and the official harness submits at the
        # step limit rather than discarding the attempt. Treating it as a failure here
        # would report a gradeable submission as no submission.
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
    # Per-task LLM spend: tokens, cost estimate, call count, and model wall time, summed from
    # this session's trace. Exact tokens; cost estimated from list price (see summarise_spend).
    result["spend"] = summarise_session_spend(str(fs_sandbox.project_root), instance_id)

    # Written where the launcher reads it back through the mount.
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


# --------------------------------------------------------------------------- #
# Launcher — one container per instance, on the host
# --------------------------------------------------------------------------- #
def inner_command(instance, args, resume: bool = False) -> str:
    """The command the launcher runs inside the task container.

    Config and task-file paths are rewritten from the host's repo root to the mount
    point, since the same checkout is visible at a different path in there.

    ``--cfg-options`` is forwarded too. The launcher parses it but the run happens
    in here, so dropping it meant a documented override — the module docstring
    advertises ``--cfg-options model_name=…`` — silently did nothing: the inner run
    fell back to the config file's default and burned a full step budget on the
    wrong model, with nothing in the launcher's log to say so.
    """
    payload = json.dumps(
        {
            key: instance.get(key, "")
            for key in ("instance_id", "repository", "language", "image_name")
        }
    )
    parts = [
        shlex.quote(sys.executable),
        shlex.quote(f"{CONTAINER_REPO}/examples/run_programbench.py"),
        "--instance-json",
        shlex.quote(payload),
        "--config",
        shlex.quote(args.config.replace(root, CONTAINER_REPO)),
        "--task-file",
        shlex.quote(args.task_file.replace(root, CONTAINER_REPO)),
    ]
    if resume:
        # Only when the launcher actually reused the workspace — so the inner run tells the
        # agent to build on the prior reconstruction rather than start over.
        parts.append("--resume")
    overrides = getattr(args, "user_cfg_options", None) or {}
    if overrides:
        parts.append("--cfg-options")
        parts.extend(shlex.quote(f"{key}={value}") for key, value in overrides.items())
    return " ".join(parts)


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
    from agentevolver.benchmark.types import Task
    from agentevolver.visual import BenchmarkMonitor

    await sandbox_manager.initialize()

    overrides = dict(getattr(args, "user_cfg_options", None) or {})
    if not overrides.get("output_owner"):
        if getattr(args, "resume", False):
            raise ValueError("--resume requires the original output_owner")
        overrides["output_owner"] = f"programbench_{time.strftime('%Y%m%d_%H%M%S')}"
        args.user_cfg_options = overrides
        config.output_owner = overrides["output_owner"]

    await benchmark_manager.configure(
        "programbench",
        base_dir=str(
            path_manager.get(P.OWNER, owner=str(config.get("output_owner") or config.tag))
            / "benchmark"
        ),
        resume=args.resume,
    )
    instances = []
    task = await benchmark_manager.reset("programbench", resume=args.resume)
    while task is not None:
        instances.append(dict(task.extra or {}))
        task = await benchmark_manager.step("programbench")
    task_ids = [t.strip() for t in args.task_ids.split(",") if t.strip()] if args.task_ids else None
    selected, warnings = select_instances(
        instances,
        task_ids=task_ids,
        start=args.start,
        end=args.end,
    )
    for warning in warnings:
        logger.warning(f"| ⚠️ {warning}")
    logger.info(f"| 📋 ProgramBench: {len(selected)}/{len(instances)} instance(s) selected")
    if not selected:
        logger.warning("| ⚠️ No instances selected — nothing to run.")
        return 0

    prefix = sys.base_prefix if sys.base_prefix != sys.prefix else sys.prefix
    mounts = {os.environ.get("AGENTEVOLVER_HOST_ROOT", root): CONTAINER_REPO, prefix: prefix}
    forwarded = {key: os.environ[key] for key in _FORWARDED_ENV if os.environ.get(key)}
    wall_clock = int(config.get("WALL_CLOCK", 21600))

    out_dir = args.out or str(
        path_manager.under(
            config.project_root,
            P.PROJECT_RESULT_RUN,
            run_id=f"programbench_{time.strftime('%Y%m%d_%H%M%S')}",
        )
    )
    os.makedirs(out_dir, exist_ok=True)
    results_path = str(path_manager.resolve_under(out_dir, f"run_{make_id()}.json"))
    results = []
    results_lock = asyncio.Lock()
    max_concurrency = max(1, int(getattr(args, "concurrency", 1) or 1))
    sem = asyncio.Semaphore(max_concurrency)
    owner = str(config.get("output_owner") or config.tag)
    monitor = BenchmarkMonitor(
        out_dir,
        "programbench",
        len(selected),
        max_concurrency,
        results_path=results_path,
        owner_dir=str(path_manager.get(P.OWNER, owner=owner)),
        title="ProgramBench",
    )
    if not args.no_monitor:
        try:
            url = await monitor.deploy(args.monitor_port)
            logger.info(f"| 📊 Benchmark monitor: {url or 'deployment failed'}")
        except Exception as error:
            logger.warning(f"| ⚠️ Benchmark monitor unavailable: {error}")

    def _write_results():
        # Rewritten after every instance finishes so a mid-run crash still leaves a usable
        # file. Called under results_lock; the write itself is atomic on the asyncio loop.
        with open(results_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "config": args.config,
                    "selection": {"task_ids": task_ids, "start": args.start, "end": args.end},
                    "concurrency": max_concurrency,
                    "records": results,
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )

    # The extension tree is shared and read-only to the run — grant it to the container uid
    # once here rather than per instance, so concurrent instances do not chown it under one
    # another. Ownership is handed back once, after every instance finishes.

    async def _run_one_instance(index, instance):
        instance_id = instance["instance_id"]
        previous = {}
        record = {"instance_id": instance_id, "status": "failed", "error": None, **previous}
        task = Task(task_id=instance_id, extra=dict(instance))
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
            prepared = await benchmark_manager.prepare("programbench", task, context=options)
            if prepared.completed:
                saved = (await benchmark_manager.stats("programbench")).extra["evaluations"][
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
                    inner_command(instance, args, resume=prepared.resumed),
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
            task = await benchmark_manager.submit("programbench", task, output=record)
            submission = task.result
            record.update(submission.get("agent_result") or {})
            task.time = time.time() - started
            task.extra.update({"workspace_dir": workspace_dir})
            monitor.task(instance_id, "grading", position=index)
            evaluated = await benchmark_manager.eval("programbench", task)
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
                "programbench", task, reclaim=getattr(args, "reclaim_disk", False)
            )
            record.setdefault("time_seconds", round(time.time() - started, 1))
            async with results_lock:
                results[:] = [item for item in results if item.get("instance_id") != instance_id]
                results.append(record)
                _write_results()
            monitor.finish_task(instance_id)
        return record

    async def _bounded(index, instance):
        # The semaphore caps how many instances hold a container at once; a crash in one
        # instance must not sink the batch, so failures come back as records, never raises.
        async with sem:
            try:
                return await _run_one_instance(index, instance)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"| ❌ [{index}] {instance.get('instance_id')} crashed: {e}", exc_info=True
                )
                rec = {
                    "instance_id": instance.get("instance_id"),
                    "status": "failed",
                    "error": str(e),
                }
                async with results_lock:
                    results.append(rec)
                    _write_results()
                    monitor.finish_task(str(instance.get("instance_id")))
                return rec

    try:
        await asyncio.gather(*[_bounded(i, inst) for i, inst in enumerate(selected, start=1)])
    except BaseException:
        monitor.close("interrupted")
        raise
    finally:
        summary = await benchmark_manager.stats("programbench")
        await benchmark_manager.cleanup("programbench")

    # Ownership is handed back once, after every container is done — doing it per instance
    # would chown the shared trees out from under instances still running concurrently.

    done = sum(1 for r in results if r["status"] == "done")
    print(f"\n✅ ProgramBench run done: {done}/{len(results)} task(s) completed")
    print(f"   Results: {results_path}")
    for r in results:
        submission = (r.get("submission") or {}).get("source", "-")
        denied = len(((r.get("egress_audit") or {}).get("denied")) or [])
        audit = r.get("reference_audit") or {}
        rules = "" if audit.get("clean", True) else "  🚨 RULES VIOLATED — score not comparable"
        spend = r.get("spend") or {}
        cost = spend.get("total_cost_usd")
        cost_str = f"~${cost:.2f}" if isinstance(cost, (int, float)) else "$ - "
        print(
            f"   {'✅' if r['status'] == 'done' else '❌'} {r['instance_id']:45} "
            f"steps={str(r.get('steps', '-')):>5}  submission={submission:12} "
            f"cost={cost_str:>8}  calls={spend.get('n_llm_calls', '-')!s:>4}  "
            f"egress-denied={denied}{rules}"
        )
    grand = summary.extra.get("usage", {}).get("total_cost_usd")
    if grand:
        print(f"   ── estimated total cost ≈ ${grand:.2f} (list-price estimate; tokens exact) ──")
    monitor.close("completed_with_errors" if summary.errors else "completed")
    return 1 if summary.errors else 0


async def main(argv=None) -> int:
    args = parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv(os.path.join(root, ".env"))
    # `config.initialize` merges every CLI argument into `args.cfg_options`, in
    # place — so after it runs, that dict also holds `task_file`, `task_ids` and
    # friends, carrying *host* paths. The launcher forwards cfg-options into the
    # container, where those paths do not exist, so snapshot what the user
    # actually asked for before it is overwritten.
    user_cfg_options = dict(args.cfg_options or {})
    config.initialize(config_path=args.config, args=args)
    args.user_cfg_options = user_cfg_options
    if args.instance_json:
        return await run_inner(args)
    logger.initialize(config=config)
    return await run_launcher(args)


if __name__ == "__main__":
    import signal

    def _terminate_via_exception(signum, _frame):
        raise KeyboardInterrupt(f"terminated by signal {signum}")

    signal.signal(signal.SIGTERM, _terminate_via_exception)
    sys.exit(asyncio.run(main()))
