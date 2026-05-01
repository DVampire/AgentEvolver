import os
import sys
import json
import asyncio
import argparse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(verbose=True)

from mmengine import DictAction

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
from src.agent import agent_manager
from src.hook import hook_manager, TraceHook
from src.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus
from src.trace import trace_manager
from src.session.types import SessionContext


def parse_args():
    parser = argparse.ArgumentParser(description="Run ReasonActAgent on a task")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "reason_act_agent.py"),
        help="Config file path",
    )
    parser.add_argument("--task", default=None, help="Override the task to run")
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        action=DictAction,
        help="Override config options in key=value format",
    )
    return parser.parse_args()


async def run_agent(record: TaskRecord):
    """TaskManager handler: executes the reason_act agent for a given TaskRecord."""
    ctx = SessionContext()
    ctx.id = record.task.session_id or ctx.id

    response = await agent_manager(
        name="reason_act_agent",
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

    # --- Trace ---
    trace_workdir = os.path.join(config.workdir, "trace")
    await trace_manager.initialize(workdir=trace_workdir)
    await trace_manager.start()
    logger.info(f"| 🌐 Trace UI: http://localhost:{trace_manager.port}")

    # --- Hooks ---
    hook_manager.register(TraceHook())

    # --- Core managers ---
    logger.info("| 📁 Initializing version manager...")
    await version_manager.initialize()

    logger.info("| 🧠 Initializing model manager...")
    await model_manager.initialize()
    logger.info(f"| ✅ Models: {await model_manager.list()}")

    logger.info("| 📁 Initializing prompt manager...")
    await prompt_manager.initialize()
    logger.info(f"| ✅ Prompts: {await prompt_manager.list()}")

    logger.info("| 📁 Initializing memory manager...")
    await memory_manager.initialize(memory_names=config.memory_names)
    logger.info(f"| ✅ Memory: {await memory_manager.list()}")

    logger.info("| 🛠️ Initializing tools...")
    await tool_manager.initialize(tool_names=config.tool_names)
    logger.info(f"| ✅ Tools: {await tool_manager.list()}")

    logger.info("| 🎯 Initializing skills...")
    skill_names = getattr(config, "skill_names", None)
    await skill_manager.initialize(skill_names=skill_names)
    logger.info(f"| ✅ Skills: {await skill_manager.list()}")

    logger.info("| 🤖 Initializing agents...")
    await agent_manager.initialize(agent_names=config.agent_names)
    logger.info(f"| ✅ Agents: {await agent_manager.list()}")

    logger.info(f"| 📋 All versions: {json.dumps(await version_manager.list(), indent=4)}")

    # --- TaskManager ---
    task_workdir = os.path.join(config.workdir, "tasks")
    await task_manager.initialize(workdir=task_workdir, handler=run_agent)
    await task_manager.start(num_workers=1)

    # --- Submit task ---
    task_text = args.task or "What is the result of 23 multiplied by 47? Please calculate it step by step."

    logger.info(f"| 📋 Submitting task: {task_text}")
    task_id = await task_manager.submit(
        content=task_text,
        category=TaskCategory.USER,
        priority=TaskPriority.HIGH,
    )
    logger.info(f"| ✅ Task submitted: {task_id}")

    # --- Wait for completion ---
    while True:
        record = await task_manager.get(task_id)
        if record and record.task.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            break
        await asyncio.sleep(1)

    record = await task_manager.get(task_id)
    if record.task.status == TaskStatus.DONE:
        logger.info(f"| ✅ Task completed: {task_id}")
    else:
        logger.error(f"| ❌ Task ended with status {record.task.status}: {record.error}")

    # --- Teardown ---
    await task_manager.stop()
    await trace_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
