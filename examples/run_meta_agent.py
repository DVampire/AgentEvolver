"""Run MetaAgent with code_agent and general_agent as sub-agents.

Usage
-----
# Default task
python examples/run_meta_agent.py

# Custom task
python examples/run_meta_agent.py --task "Write a Python function to reverse a string and add unit tests."

# Override config options
python examples/run_meta_agent.py --task "..." --cfg-options model_name=openai/o3

# Launch another registered orchestrator through the same lifecycle
python examples/run_meta_agent.py --config configs/website_evolution_demo.py \
    --agent-name website_builder_agent --task "..."
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

from agentevolver.config import config
from agentevolver.paths import P, path_manager
from agentevolver.logger import logger
from agentevolver.model import model_manager
from agentevolver.version import version_manager
from agentevolver.prompt import prompt_manager
from agentevolver.memory import memory_manager
from agentevolver.tool import tool_manager
from agentevolver.skill import skill_manager
from agentevolver.connector import connector_manager
from agentevolver.environment import environment_manager
from agentevolver.agent import agent_manager
from agentevolver.extension import extension_manager
from agentevolver.hook import hook_manager
from agentevolver.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus, add_task_args, resolve_task
from agentevolver.trace import trace_manager
from agentevolver.trajectory import trajectory_manager
from agentevolver.session.types import SessionContext
from agentevolver.session.project import ensure_session_sandbox, bind_session_roots
from agentevolver.utils import make_id


def parse_args():
    parser = argparse.ArgumentParser(description="Run MetaAgent on a task")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "meta_agent.py"),
        help="Config file path",
    )
    parser.add_argument(
        "--agent-name",
        default="meta_agent",
        help="Registered orchestrator to run (default: meta_agent).",
    )
    add_task_args(parser, default_task_file=os.path.join(root, "examples", "tasks", "calculator_tool.html"))
    parser.add_argument(
        "--plugins",
        nargs="*",
        default=None,
        metavar="NAME",
        help=(
            "Plugins this run may call, e.g. --plugins arxiv wikipedia. The registry "
            "holds hundreds of tools for services most runs never touch, so a plugin "
            "reaches the model only when the run names it."
        ),
    )
    parser.add_argument(
        "--plan-mode",
        choices=["off", "auto", "plan"],
        default="auto",
        help=(
            "How much planning this run is held to. `auto` (the default) leaves it to "
            "the agent: it keeps plan.md current for anything past one obvious step, and "
            "nothing is gated. `plan` refuses every action that changes anything until "
            "you approve a plan through `exit_plan_mode` — reading and reasoning are "
            "unaffected. `off` asks for nothing. Before this flag a script run could "
            "only reach one of the three: `plan.set` is a gateway command, so plan mode "
            "was unreachable outside the UI."
        ),
    )
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        action=DictAction,
        help="Override config options in key=value format",
    )
    return parser.parse_args()


async def answer_from_the_terminal(session_id: str, stop: asyncio.Event) -> None:
    """Answer the agent's questions from this terminal, while a script run has nobody else.

    `ask_user_question` and `exit_plan_mode` suspend on a rendezvous the gateway
    normally resolves from the UI. A script run has no UI, so before this the agent put
    its plan up for review and waited out `DEFAULT_QUESTION_TIMEOUT_S` — an hour of
    silence, then a timeout, then the gate still shut. Nobody was listening; the run
    just took an hour to find out.

    A person running this command *is* here, so ask them. Runs until the task ends
    rather than once, because a run may ask more than once — a declined plan is
    followed by another.
    """
    from agentevolver.conversation.question import question_manager
    from agentevolver.conversation.types import UserAnswer

    while not stop.is_set():
        for record in question_manager.pending(session_id):
            for question in record.questions:
                print(f"\n{'=' * 70}\n{question.header or 'Question'}: {question.question}")
                if question.detail:
                    print(f"\n{question.detail}")
                labels = [option.label for option in question.options]
                for index, option in enumerate(question.options, 1):
                    suffix = f" — {option.description}" if option.description else ""
                    print(f"  {index}. {option.label}{suffix}")
                # Read in a thread: `input()` would block the loop the agent runs on,
                # so the answer would arrive only after everything else had finished.
                raw = (await asyncio.to_thread(input, "\nAnswer (number, or free text): ")).strip()
                if raw.isdigit() and 1 <= int(raw) <= len(labels):
                    answer = UserAnswer(id=question.id, selected=[labels[int(raw) - 1]])
                else:
                    answer = UserAnswer(id=question.id, custom=raw)
                question_manager.answer(record.id, [answer])
        await asyncio.sleep(0.5)


async def run_agent(record: TaskRecord, ctx: SessionContext, agent_name: str):

    response = await agent_manager(
        name=agent_name,
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
    # A direct script is a session too: bind all runtime state before managers
    # initialize, so no tag-level log/workspace directories are created.
    session_id = make_id()
    ctx = SessionContext(id=session_id, name="main_entrypoint")
    # Same directory the gateway would use for this session — see agentevolver/paths.
    sandbox = ensure_session_sandbox(
        ctx,
        shared_extension_root=config.extension_root,
    )
    bind_session_roots(config, sandbox)
    # Named here rather than in the config, because which plugins a run may call is a
    # property of the run. `Agent._get_*_context` and the native projection both read it
    # off the context.
    if args.plugins:
        ctx.extra = dict(getattr(ctx, "extra", None) or {})
        ctx.extra["plugin_allowlist"] = list(args.plugins)
    extension_manager.set_base_dir(config.extension_root)
    logger.initialize(config=config)
    logger.info(f"| Config: {config.pretty_text}")

    # Validate the assembled config (duplicate whitelist entries silently shadow).
    from agentevolver.config import validate_assembly
    for _problem in validate_assembly(config):
        logger.warning(f"| ⚠️ Config: {_problem}")

    # --- Core managers ---
    logger.info("| 📁 Initializing version manager...")
    await version_manager.initialize()
    logger.info(f"| ✅ Versions: {await version_manager.list()}")

    # --- Trace ---
    await trace_manager.initialize()
    await trace_manager.start()

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

    plugin_names = args.plugins if args.plugins is not None else getattr(config, "plugin_names", None)
    if plugin_names:
        logger.info("| 🧩 Initializing plugins...")
        from agentevolver.plugins import plugin_manager
        await plugin_manager.initialize(plugin_names=plugin_names)
        logger.info(f"| ✅ Plugins: {await plugin_manager.list()}")

    logger.info("| 🤖 Initializing agents...")
    await agent_manager.initialize(agent_names=config.agent_names)
    logger.info(f"| ✅ Agents: {await agent_manager.list()}")

    # Layer hot-pluggable extensions (external `extension/`) on top of built-ins.
    _ext_manifest = await extension_manager.initialize()
    logger.info(f"| ✅ Extensions loaded: {[f'{c.module}:{c.name}' for c in _ext_manifest.components]}")

    logger.info(f"| 📋 All versions: {json.dumps(await version_manager.list(), indent=4)}")

    # --- TaskManager ---
    task_log_root = str(path_manager.under(config.log_root, P.LOG_MODULE, module="tasks"))
    await task_manager.initialize(
        log_root=task_log_root,
        handler=lambda record: run_agent(record, ctx, args.agent_name),
    )
    await task_manager.start(num_workers=1)

    # --- Build the task (inline --task string or --task-file document) ---
    task_text, task_files, task_metadata = resolve_task(args, task_log_root)
    logger.info(f"| 📋 Submitting task:\n{task_text}")
    task_id = await task_manager.submit(
        content=task_text,
        category=TaskCategory.USER,
        priority=TaskPriority.HIGH,
        files=task_files,
        metadata=task_metadata,
    )
    logger.info(f"| ✅ Session id: {session_id}, Task id: {task_id}")

    # After submit, so the stance is set before the first turn runs. Setting it earlier
    # would work too; setting it later is a race the agent wins by acting first.
    from agentevolver.plan import PlanMode, plan_manager
    plan_manager.set_mode(session_id, PlanMode(args.plan_mode))
    if args.plan_mode == "plan":
        logger.info(f"| 📋 Plan mode: plan. Nothing with effects runs until a plan is "
                    f"approved — answer the review in this terminal.")
    else:
        logger.info(f"| 📋 Plan mode: {args.plan_mode}")

    # Answering runs beside the task, not after it: the agent suspends mid-run waiting
    # for the reply, so a loop that started once the task finished would never start.
    answering_stop = asyncio.Event()
    answering = asyncio.create_task(
        answer_from_the_terminal(session_id, answering_stop), name="terminal-answers")

    # --- Wait for completion ---
    while True:
        record = await task_manager.get(task_id)
        if record and record.task.status in (
            TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED
        ):
            break
        await asyncio.sleep(1)

    answering_stop.set()
    answering.cancel()

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
    # Clean up environments (browser peer containers) and the sandbox subsystem while
    # the event loop and the opensandbox SDK executor are still alive. Relying on the
    # asyncio-atexit cleanups instead fails at process exit ("Executor shutdown has
    # been called"), leaking peer containers.
    await task_manager.stop()
    try:
        await environment_manager.cleanup()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"| ⚠️ environment cleanup: {e}")
    try:
        from agentevolver.sandbox import sandbox_manager
        await sandbox_manager.cleanup()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"| ⚠️ sandbox cleanup: {e}")
    await trace_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
