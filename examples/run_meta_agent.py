"""Run MetaAgent with code_agent and general_agent as sub-agents.

Usage
-----
# Default task
python examples/run_meta_agent.py

# Custom task
python examples/run_meta_agent.py --task "Write a Python function to reverse a string and add unit tests."

# Override config options
python examples/run_meta_agent.py --task "..." --cfg-options model_name=openai/o3
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(verbose=True)

from mmengine import DictAction
import argparse

root = str(Path(__file__).resolve().parents[1])
sys.path.append(root)

from src.config import config
from src.logger import logger
from src.model import model_manager
from src.version import version_manager
from src.prompt import prompt_manager
from src.memory import memory_manager
from src.tool import tool_manager
from src.skill import skill_manager
from src.connector import connector_manager
from src.environment import environment_manager
from src.agent import agent_manager
from src.extension import extension_manager
from src.hook import hook_manager
from src.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus, add_task_args, resolve_task
from src.trace import trace_manager
from src.trajectory import trajectory_manager
from src.session.types import SessionContext
from src.utils import make_id


def parse_args():
    parser = argparse.ArgumentParser(description="Run MetaAgent on a task")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "meta_agent.py"),
        help="Config file path",
    )
    add_task_args(parser, default_task_file=os.path.join(root, "examples", "tasks", "calculator_tool.html"))
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        action=DictAction,
        help="Override config options in key=value format",
    )
    return parser.parse_args()


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
    logger.initialize(config=config)
    logger.info(f"| Config: {config.pretty_text}")

    # Validate the assembled config (duplicate whitelist entries silently shadow).
    from src.config import validate_assembly
    for _problem in validate_assembly(config):
        logger.warning(f"| ⚠️ Config: {_problem}")

    # --- Core managers ---
    logger.info("| 📁 Initializing version manager...")
    await version_manager.initialize()
    logger.info(f"| ✅ Versions: {await version_manager.list()}")

    # --- Trace ---
    await trace_manager.initialize()
    await trace_manager.start()
    logger.info(f"| 🌐 Trace UI: http://localhost:{trace_manager.port}")

    # --- Trajectory (training-data capture; fed by trajectory_hook) ---
    await trajectory_manager.initialize()

    # --- Hooks ---
    await hook_manager.initialize()

    logger.info("| 🧠 Initializing model manager...")
    await model_manager.initialize()
    logger.info(f"| ✅ Models: {model_manager.list()}")

    logger.info("| 📁 Initializing prompt manager...")
    await prompt_manager.initialize()
    logger.info(f"| ✅ Prompts: {await prompt_manager.list()}")

    logger.info("| 📁 Initializing memory manager...")
    await memory_manager.initialize(memory_names=config.memory_names)
    logger.info(f"| ✅ Memory: {await memory_manager.list()}")

    logger.info("| 🛠️  Initializing tools...")
    await tool_manager.initialize(tool_names=config.tool_names)
    logger.info(f"| ✅ Tools: {await tool_manager.list()}")

    logger.info("| 🎯 Initializing skills...")
    skill_names = getattr(config, "skill_names", None)
    await skill_manager.initialize(skill_names=skill_names)
    logger.info(f"| ✅ Skills: {await skill_manager.list()}")

    logger.info("| 🔌 Initializing connectors...")
    connector_names = getattr(config, "connector_names", None)
    await connector_manager.initialize(connector_names=connector_names)
    logger.info(f"| ✅ Connectors: {await connector_manager.list()}")

    logger.info("| 🌐 Initializing environments...")
    env_names = getattr(config, "env_names", None)
    if env_names:
        await environment_manager.initialize(env_names=env_names)
        logger.info(f"| ✅ Environments: {await environment_manager.list()}")

    logger.info("| 🤖 Initializing agents...")
    await agent_manager.initialize(agent_names=config.agent_names)
    logger.info(f"| ✅ Agents: {await agent_manager.list()}")

    # Layer hot-pluggable extensions (external `extension/`) on top of built-ins.
    _ext_manifest = await extension_manager.initialize()
    logger.info(f"| ✅ Extensions loaded: {[f'{c.module}:{c.name}' for c in _ext_manifest.components]}")

    logger.info(f"| 📋 All versions: {json.dumps(await version_manager.list(), indent=4)}")

    # --- Session context ---
    session_id = make_id()
    name = f"main_entrypoint"
    ctx = SessionContext(
        id=session_id,
        name=name,
    )

    # --- TaskManager ---
    task_work_dir = os.path.join(config.run_dir, "tasks")
    await task_manager.initialize(work_dir=task_work_dir, handler=lambda record: run_agent(record, ctx))
    await task_manager.start(num_workers=1)

    # --- Build the task (inline --task string or --task-file document) ---
    task_text, task_files, task_metadata = resolve_task(args, task_work_dir)
    logger.info(f"| 📋 Submitting task:\n{task_text}")
    task_id = await task_manager.submit(
        content=task_text,
        category=TaskCategory.USER,
        priority=TaskPriority.HIGH,
        files=task_files,
        metadata=task_metadata,
    )
    logger.info(f"| ✅ Session id: {session_id}, Task id: {task_id}")

    # --- Wait for completion ---
    while True:
        record = await task_manager.get(task_id)
        if record and record.task.status in (
            TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED
        ):
            break
        await asyncio.sleep(1)

    record = await task_manager.get(task_id)
    if record.task.status == TaskStatus.DONE:
        logger.info(f"| ✅ Task completed: {task_id}")
        logger.info(f"| 📄 Result:\n{record.result}")
    else:
        logger.error(f"| ❌ Task ended with status {record.task.status}: {record.error}")

    # --- Print memory HTML path if available ---
    # Response.extra / Response.data are Optional[dict]; guard against None.
    if record.result is not None:
        extra = getattr(record.result, "extra", None)
        data = getattr(record.result, "data", None)
        memory_path = None
        for src in (extra, data):
            if isinstance(src, dict) and src.get("memory_path"):
                memory_path = src["memory_path"]
                break
        if memory_path:
            logger.info(f"| 📄 Memory HTML: {memory_path}")

    # --- Teardown ---
    await task_manager.stop()
    await trace_manager.stop()
    await asyncio.sleep(600)  # Wait for all tasks to finish


if __name__ == "__main__":
    asyncio.run(main())
