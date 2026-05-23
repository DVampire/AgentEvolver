"""Run ToolEvaluateAgent on a tool."""
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
from src.tool import tool_manager
from src.memory import memory_manager
from src.skill import skill_manager
from src.agent import agent_manager
from src.hook import hook_manager
from src.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus
from src.trace import trace_manager
from src.session.types import SessionContext


def parse_args():
    parser = argparse.ArgumentParser(description="Run ToolEvaluateAgent to evaluate a tool")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "tool_evaluate_agent.py"),
        help="Config file path",
    )
    parser.add_argument(
        "--task",
        default=(
            "Evaluate the hello_world_tool."
        ),
        help="Evaluation task description",
    )
    parser.add_argument("--tool-name", default="hello_world_tool", help="Name of the tool to evaluate")
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        action=DictAction,
        help="Override config options in key=value format",
    )
    return parser.parse_args()


async def run_evaluate_agent(record: TaskRecord):
    """TaskManager handler: executes the tool evaluate agent for a given TaskRecord."""
    ctx = SessionContext()
    ctx.id = record.task.session_id or ctx.id

    target_name = (record.task.metadata or {}).get("target_name", "hello_world_tool")

    response = await agent_manager(
        name="tool_evaluate_agent",
        input={"task": record.task.content, "target_name": target_name},
        ctx=ctx,
        workdir=config.workdir,
    )
    return response


async def main():
    args = parse_args()

    config.initialize(config_path=args.config, args=args)
    logger.initialize(config=config)
    logger.info(f"| Config: {config.pretty_text}")

    logger.info("| 📁 Initializing version manager...")
    await version_manager.initialize()
    logger.info(f"| ✅ Versions: {await version_manager.list()}")

    logger.info("| 🌐 Initializing trace manager...")
    await trace_manager.initialize()
    await trace_manager.start()
    logger.info(f"| 🌐 Trace UI: http://localhost:{trace_manager.port}")

    logger.info("| 🪝 Initializing hook manager...")
    await hook_manager.initialize()

    logger.info("| 🧠 Initializing model manager...")
    await model_manager.initialize()
    logger.info(f"| ✅ Models: {model_manager.list()}")

    logger.info("| 📁 Initializing prompt manager...")
    await prompt_manager.initialize()
    logger.info(f"| ✅ Prompts: {await prompt_manager.list()}")

    logger.info("| 🧠 Initializing memory manager...")
    await memory_manager.initialize(memory_names=getattr(config, "memory_names", None))
    logger.info(f"| ✅ Memory: {await memory_manager.list()}")

    logger.info("| 🛠️ Initializing tools...")
    await tool_manager.initialize(tool_names=config.tool_names)
    logger.info(f"| ✅ Tools: {await tool_manager.list()}")

    logger.info("| 🎯 Initializing skills...")
    await skill_manager.initialize(skill_names=getattr(config, "skill_names", None))
    logger.info(f"| ✅ Skills: {await skill_manager.list()}")

    logger.info("| 🤖 Initializing agent manager...")
    await agent_manager.initialize(agent_names=config.agent_names)
    logger.info(f"| ✅ Agents: {await agent_manager.list()}")

    logger.info(f"| 📋 All versions: {json.dumps(await version_manager.list(), indent=4)}")

    # --- TaskManager ---
    task_workdir = os.path.join(config.workdir, "tasks")
    await task_manager.initialize(workdir=task_workdir, handler=run_evaluate_agent)
    await task_manager.start(num_workers=1)

    # --- Submit task ---
    task_text = args.task
    target_name = args.tool_name

    logger.info(f"| 📋 Submitting evaluation task: target={target_name}")
    logger.info(f"| 📋 Task: {task_text}")

    task_id = await task_manager.submit(
        content=task_text,
        category=TaskCategory.USER,
        priority=TaskPriority.HIGH,
        metadata={"target_name": target_name},
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
        logger.info(f"| ✅ Evaluation completed: {task_id}")
    else:
        logger.error(f"| ❌ Evaluation ended with status {record.task.status}: {record.error}")

    # --- Teardown ---
    await task_manager.stop()
    await asyncio.sleep(600)  # Keep trace UI alive for inspection
    await trace_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
