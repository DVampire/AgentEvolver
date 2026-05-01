import os
import sys
import json
import argparse
import asyncio
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
from src.session.types import SessionContext


def parse_args():
    parser = argparse.ArgumentParser(description="Run CodeAgent on a coding task")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "code_agent.py"),
        help="Config file path",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Override the task to run (optional)",
    )
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        action=DictAction,
        help="Override config options in key=value format",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    config.initialize(config_path=args.config, args=args)
    logger.initialize(config=config)
    logger.info(f"| Config: {config.pretty_text}")

    # Initialize all managers
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

    # Task — override via --task or --repo, otherwise use defaults below
    task = "Generate fibonacci sequence generator in Python and calculate the 15th term."  # Default task

    files = []

    logger.info(f"| 📋 Task: {task}")
    logger.info(f"| 📂 Files: {files}")

    ctx = SessionContext()

    await agent_manager(
        name="code_agent",
        input={
            "task": task, 
            "files": files,
            "workdir": config.workdir,
        },
        ctx=ctx,
    )


if __name__ == "__main__":
    asyncio.run(main())
