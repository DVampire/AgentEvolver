"""Run EnvironmentEvaluateAgent to evaluate a generated environment."""
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

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.model import model_manager
from agentevolver.version import version_manager
from agentevolver.prompt import prompt_manager
from agentevolver.tool import tool_manager
from agentevolver.memory import memory_manager
from agentevolver.skill import skill_manager
from agentevolver.connector import connector_manager
from agentevolver.environment import environment_manager
from agentevolver.agent import agent_manager
from agentevolver.hook import hook_manager
from agentevolver.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus, add_task_args, resolve_task
from agentevolver.trace import trace_manager
from agentevolver.session.types import SessionContext
from agentevolver.utils import make_id


def parse_args():
    parser = argparse.ArgumentParser(description="Run EnvironmentEvaluateAgent to evaluate a generated environment")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "environment_evaluate_agent.py"),
        help="Config file path",
    )
    add_task_args(parser, default_task_file=os.path.join(root, "examples", "tasks", "environment_evaluate.html"))
    parser.add_argument("--environment-name", default="counter_environment", help="Name of the environment to evaluate")
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        action=DictAction,
        help="Override config options in key=value format",
    )
    return parser.parse_args()


async def run_evaluate_environment(record: TaskRecord):
    """TaskManager handler: executes the environment evaluate agent for a given TaskRecord."""
    session_id = record.task.session_id or make_id()
    ctx = SessionContext(id=session_id)

    target_name = (record.task.metadata or {}).get("target_name")

    response = await agent_manager(
        name="environment_evaluate_agent",
        input={"task": record.task.content, "target_name": target_name},
        ctx=ctx,
        workspace_root=config.workspace_root,
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

    logger.info("| 🔌 Initializing connectors...")
    connector_names = getattr(config, "connector_names", None)
    await connector_manager.initialize(connector_names=connector_names)
    logger.info(f"| ✅ Connectors: {await connector_manager.list()}")

    logger.info("| 🌐 Initializing environment manager...")
    await environment_manager.initialize(env_names=[args.environment_name])
    logger.info(f"| ✅ Environments: {await environment_manager.list()}")

    logger.info("| 🤖 Initializing agent manager...")
    await agent_manager.initialize(agent_names=config.agent_names)
    logger.info(f"| ✅ Agents: {await agent_manager.list()}")

    logger.info(f"| 📋 All versions: {json.dumps(await version_manager.list(), indent=4)}")

    # --- TaskManager ---
    task_log_root = os.path.join(config.log_root, "tasks")
    await task_manager.initialize(log_root=task_log_root, handler=run_evaluate_environment)
    await task_manager.start(num_workers=1)

    # --- Submit task ---
    task_text, task_files, task_doc_meta = resolve_task(args, task_log_root)
    target_name = args.environment_name

    logger.info(f"| 📋 Submitting environment evaluation task: target={target_name}")
    logger.info(f"| 📋 Task: {task_text}")

    task_id = await task_manager.submit(
        content=task_text,
        category=TaskCategory.USER,
        priority=TaskPriority.HIGH,
        files=task_files,
        metadata={"target_name": target_name, **(task_doc_meta or {})},
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
        logger.info(f"| ✅ Environment evaluation completed: {task_id}")
        logger.info(f"| 📄 Result:\n{record.result}")
    else:
        logger.error(f"| ❌ Evaluation ended with status {record.task.status}: {record.error}")

    # --- Teardown ---
    await task_manager.stop()
    await trace_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
