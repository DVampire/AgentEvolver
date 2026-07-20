"""Run MetaAgent on selected ProgramBench tasks, modeled on run_meta_agent.py.

Runs tasks only — no scoring. See agentevolver/benchmark/default/programbench.py
for the existing `programbench eval`-based scorer, which can be pointed at this
script's output workspaces separately.

The agent works inside the plain filesystem session sandbox — no per-instance
Docker binding yet (image_name is only referenced in the task prompt text). See
docs/superpowers/specs/2026-07-20-programbench-runner-design.md §10 for the
`sandbox_manager.acquire("opensandbox", image=...)` follow-up.

Usage
-----
# Run two named instances, with self-evolution on (default)
python examples/run_programbench.py --task-ids <instance_id_1>,<instance_id_2>

# Run the first 5 instances (by load order), self-evolution off
python examples/run_programbench.py --start 0 --end 5 --no-evolve

# Override config options
python examples/run_programbench.py --start 0 --end 1 --cfg-options model_name=openai/o3
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(verbose=True)

from mmengine import DictAction

root = str(Path(__file__).resolve().parents[1])
sys.path.append(root)

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.model import model_manager
from agentevolver.version import version_manager
from agentevolver.prompt import prompt_manager
from agentevolver.memory import memory_manager
from agentevolver.tool import tool_manager
from agentevolver.skill import skill_manager
from agentevolver.connector import connector_manager
from agentevolver.agent import agent_manager
from agentevolver.extension import extension_manager
from agentevolver.hook import hook_manager
from agentevolver.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus
from agentevolver.trace import trace_manager
from agentevolver.trajectory import trajectory_manager
from agentevolver.session.types import SessionContext
from agentevolver.session.project import ensure_session_sandbox, bind_session_roots
from agentevolver.utils import make_id, dedent
from agentevolver.benchmark.default.programbench import SYSTEM_PROMPT


EVOLVE_AGENT_NAMES = [
    "tool_optimize_agent", "tool_evaluate_agent", "tool_generate_agent",
    "agent_generate_agent", "agent_optimize_agent", "agent_evaluate_agent",
    "skill_generate_agent", "skill_optimize_agent", "skill_evaluate_agent",
    "environment_generate_agent", "environment_optimize_agent", "environment_evaluate_agent",
    "connector_generate_agent", "connector_optimize_agent", "connector_evaluate_agent",
]
EVOLVE_TOOL_NAMES = ["evolution_tool"]
EVOLVE_SKILL_NAMES = [
    "self_evolving_skill",
    "agent_creator_skill", "tool_creator_skill", "environment_creator_skill",
    "skill_creator_skill", "connector_creator_skill",
]


def extend_roster_for_evolve(agent_names, tool_names, skill_names, evolve):
    """Extend the base roster with the self-evolution add-ons when `evolve` is True.

    Returns fresh (agent_names, tool_names, skill_names) lists; the inputs are
    never mutated in place.
    """
    agent_names = list(agent_names)
    tool_names = list(tool_names)
    skill_names = list(skill_names)
    if evolve:
        agent_names += [n for n in EVOLVE_AGENT_NAMES if n not in agent_names]
        tool_names += [n for n in EVOLVE_TOOL_NAMES if n not in tool_names]
        skill_names += [n for n in EVOLVE_SKILL_NAMES if n not in skill_names]
    return agent_names, tool_names, skill_names


def select_instances(instances, task_ids=None, start=None, end=None):
    """Select a subset of ProgramBench instances by id list and/or index range.

    `task_ids` takes precedence over `start`/`end` (a warning is returned, not
    raised, if both are given). Unknown ids are skipped, also as a warning.
    Raises ValueError if neither selector is given.

    Returns (selected: list[dict], warnings: list[str]).
    """
    if not task_ids and start is None and end is None:
        raise ValueError("select_instances requires task_ids or start/end")

    warnings = []
    if task_ids:
        if start is not None or end is not None:
            warnings.append("--start/--end ignored because --task-ids was given")
        by_id = {inst["instance_id"]: inst for inst in instances}
        selected = [by_id[tid] for tid in task_ids if tid in by_id]
        unknown = [tid for tid in task_ids if tid not in by_id]
        if unknown:
            warnings.append(f"unknown task id(s) skipped: {unknown}")
        return selected, warnings

    return list(instances[start:end]), warnings


def build_task_content(instance):
    """Build the MetaAgent task content for one ProgramBench instance.

    Folds ProgramBenchmark's SYSTEM_PROMPT into the content (agent_manager has
    no separate system-prompt override hook here, matching how
    run_meta_agent.py / run_hle.py already thread only task content through).
    """
    question = dedent(f"""
        Reconstruct the program `{instance.get('repository', '')}` (language: {instance.get('language', '')}).

        A compiled binary and its documentation are available in your sandbox
        (task image: `{instance.get('image_name', '')}`, commit `{instance.get('commit', '')}`). Implement a complete
        codebase that reproduces the program's behavior. Produce the full source
        tree as your final answer.
    """)
    return f"{SYSTEM_PROMPT}\n\n{question}"


def parse_args():
    parser = argparse.ArgumentParser(description="Run MetaAgent on selected ProgramBench tasks")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "programbench_agent.py"),
        help="Config file path",
    )
    parser.add_argument("--task-ids", default=None, help="Comma-separated ProgramBench instance_id list")
    parser.add_argument("--start", type=int, default=None, help="Start index (inclusive) into the loaded instance list")
    parser.add_argument("--end", type=int, default=None, help="End index (exclusive) into the loaded instance list")
    parser.add_argument(
        "--evolve", action=argparse.BooleanOptionalAction, default=True,
        help="Include the self-evolution agent/skill/tool roster (default: on; use --no-evolve to disable).",
    )
    parser.add_argument("--out", default=None, help="Results JSON output directory")
    parser.add_argument(
        "--cfg-options", nargs="+", action=DictAction,
        help="Override config options in key=value format",
    )
    args = parser.parse_args()
    if not args.task_ids and args.start is None and args.end is None:
        parser.error("specify --task-ids or --start/--end; refusing to run the entire benchmark by default")
    return args


async def run_agent(record: TaskRecord, ctx: SessionContext):
    response = await agent_manager(
        name="meta_agent",
        input={
            "task": record.task.content,
            "files": record.task.files,
        },
        ctx=ctx,
    )
    return response


async def main():
    args = parse_args()
    config.initialize(config_path=args.config, args=args)

    config.agent_names, config.tool_names, config.skill_names = extend_roster_for_evolve(
        config.agent_names, config.tool_names, config.skill_names, args.evolve,
    )

    # Capture the fixed bootstrap roots before any session rebases them — every
    # per-task session below is created under these same original roots, never
    # under the previous task's (already-rebased) config.project_root.
    bootstrap_project_root = config.project_root
    bootstrap_extension_root = config.extension_root

    bootstrap_session_id = make_id()
    bootstrap_ctx = SessionContext(id=bootstrap_session_id, name="programbench_bootstrap")
    bootstrap_sandbox = ensure_session_sandbox(
        bootstrap_ctx, bootstrap_project_root, shared_extension_root=bootstrap_extension_root,
    )
    bind_session_roots(config, bootstrap_sandbox)
    bootstrap_log_root = config.log_root
    extension_manager.set_base_dir(bootstrap_extension_root)
    logger.initialize(config=config)
    logger.info(f"| Config: {config.pretty_text}")

    from agentevolver.config import validate_assembly
    for _problem in validate_assembly(config):
        logger.warning(f"| ⚠️ Config: {_problem}")

    logger.info("| 📁 Initializing version manager...")
    await version_manager.initialize()
    logger.info(f"| ✅ Versions: {await version_manager.list()}")

    await trace_manager.initialize()
    await trace_manager.start()
    logger.info(f"| 🌐 Trace UI: http://localhost:{trace_manager.port}")

    await trajectory_manager.initialize()
    await hook_manager.initialize()

    logger.info("| 🧠 Initializing model manager...")
    await model_manager.initialize()
    logger.info(f"| ✅ Models: {model_manager.list()}")

    logger.info("| 📁 Initializing prompt manager...")
    await prompt_manager.initialize()

    logger.info("| 📁 Initializing memory manager...")
    await memory_manager.initialize(memory_names=config.memory_names)

    logger.info("| 🛠️  Initializing tools...")
    await tool_manager.initialize(tool_names=config.tool_names)
    logger.info(f"| ✅ Tools: {await tool_manager.list()}")

    logger.info("| 🎯 Initializing skills...")
    await skill_manager.initialize(skill_names=config.skill_names)
    logger.info(f"| ✅ Skills: {await skill_manager.list()}")

    logger.info("| 🔌 Initializing connectors...")
    await connector_manager.initialize(connector_names=config.connector_names)
    logger.info(f"| ✅ Connectors: {await connector_manager.list()}")

    logger.info("| 🤖 Initializing agents...")
    await agent_manager.initialize(agent_names=config.agent_names)
    logger.info(f"| ✅ Agents: {await agent_manager.list()}")

    _ext_manifest = await extension_manager.initialize()
    logger.info(f"| ✅ Extensions loaded: {[f'{c.module}:{c.name}' for c in _ext_manifest.components]}")

    # TaskManager: one fixed log_root for the whole batch run (not rebased per
    # task — its bookkeeping covers every submitted task in one place).
    task_log_root = os.path.join(bootstrap_log_root, "tasks")
    await task_manager.initialize(log_root=task_log_root, handler=None)
    await task_manager.start(num_workers=1)

    # --- Load & select ProgramBench instances ---
    from agentevolver.data.programbench import ProgramBenchDataset
    dataset = ProgramBenchDataset()
    task_ids = [t.strip() for t in args.task_ids.split(",") if t.strip()] if args.task_ids else None
    selected, selection_warnings = select_instances(
        dataset.data, task_ids=task_ids, start=args.start, end=args.end,
    )
    for w in selection_warnings:
        logger.warning(f"| ⚠️ {w}")
    logger.info(f"| 📋 ProgramBench: {len(selected)}/{len(dataset.data)} instance(s) selected")

    if not selected:
        logger.warning("| ⚠️ No instances selected — nothing to run.")
        await task_manager.stop()
        await trace_manager.stop()
        return

    # --- Run each selected instance sequentially, in its own session sandbox ---
    out_dir = args.out or os.path.join(bootstrap_log_root, "results", "programbench")
    os.makedirs(out_dir, exist_ok=True)
    results_path = os.path.join(out_dir, f"run_{bootstrap_session_id}.json")
    results = []

    for i, instance in enumerate(selected, start=1):
        instance_id = instance["instance_id"]
        logger.info(f"| ▶️ [{i}/{len(selected)}] Starting ProgramBench task: {instance_id}")

        session_id = make_id()
        ctx = SessionContext(id=session_id, name=f"programbench_{instance_id}")
        sandbox = ensure_session_sandbox(
            ctx, bootstrap_project_root, shared_extension_root=bootstrap_extension_root,
        )
        bind_session_roots(config, sandbox)
        task_manager.set_handler(lambda record, _ctx=ctx: run_agent(record, _ctx))

        content = build_task_content(instance)
        t0 = time.time()
        status_value = "failed"
        error_message = None
        try:
            task_id = await task_manager.submit(
                content=content,
                category=TaskCategory.USER,
                priority=TaskPriority.HIGH,
                metadata={
                    "instance_id": instance_id,
                    "repository": instance.get("repository", ""),
                    "language": instance.get("language", ""),
                    "image_name": instance.get("image_name", ""),
                },
                session_id=session_id,
            )
            while True:
                record = await task_manager.get(task_id)
                if record and record.task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    break
                await asyncio.sleep(1)
            status_value = record.task.status.value
            error_message = record.error
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ [{instance_id}] runner-level failure: {e}", exc_info=True)
            error_message = str(e)
        elapsed = time.time() - t0

        mark = "✅" if status_value == "done" else "❌"
        logger.info(f"| {mark} [{i}/{len(selected)}] {instance_id} finished as {status_value} in {elapsed:.1f}s")

        results.append({
            "instance_id": instance_id,
            "status": status_value,
            "time_seconds": round(elapsed, 1),
            "session_id": session_id,
            "workspace_path": str(sandbox.workspace_root),
            "error": error_message,
        })

        # Write after every task so a mid-run crash still leaves a usable partial file.
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump({
                "config": args.config,
                "evolve": args.evolve,
                "selection": {"task_ids": task_ids, "start": args.start, "end": args.end},
                "records": results,
            }, f, ensure_ascii=False, indent=2)

    done_count = sum(1 for r in results if r["status"] == "done")
    print(f"\n✅ ProgramBench run done: {done_count}/{len(results)} task(s) completed")
    print(f"   Results: {results_path}")

    await task_manager.stop()
    await trace_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
