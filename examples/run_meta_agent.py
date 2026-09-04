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
import signal
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
from agentevolver.plugins import plugin_manager
from agentevolver.workflow import workflow_manager
from agentevolver.environment import environment_manager
from agentevolver.agent import agent_manager
from agentevolver.extension import extension_manager
from agentevolver.hook import hook_manager
from agentevolver.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus, add_task_args, resolve_task
from agentevolver.trace import trace_manager
from agentevolver.trajectory import trajectory_manager
from agentevolver.session.types import SessionContext
from agentevolver.session.context import bind_session_roots, ensure_session_sandbox
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
                # Register stdin with the event loop instead of parking `input()` in an
                # executor thread. A blocked executor read cannot be cancelled on SIGTERM
                # and used to keep an otherwise-clean launcher alive indefinitely.
                print("\nAnswer (number, or free text): ", end="", flush=True)
                loop = asyncio.get_running_loop()
                ready = loop.create_future()
                fd = sys.stdin.fileno()

                def read_line() -> None:
                    if not ready.done():
                        ready.set_result(sys.stdin.readline())

                loop.add_reader(fd, read_line)
                try:
                    line = await ready
                finally:
                    loop.remove_reader(fd)
                if line == "":
                    stop.set()
                    return
                raw = line.strip()
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


async def teardown() -> None:
    """Release one launcher run in dependency order; safe after partial startup."""
    from agentevolver.deploy import deployment_manager
    from agentevolver.runtime import kernel

    cleanups = (
        ("task", task_manager.stop),
        ("runtime", kernel.shutdown),
        ("environment", environment_manager.cleanup),
        ("deployment", deployment_manager.cleanup),
        ("workflow", workflow_manager.cleanup),
        ("plugin", plugin_manager.cleanup),
    )
    for label, cleanup in cleanups:
        try:
            await cleanup()
        except Exception as error:  # noqa: BLE001 -- teardown must continue
            logger.warning(f"| ⚠️ {label} cleanup: {error}")
    try:
        from agentevolver.sandbox import sandbox_manager
        await sandbox_manager.cleanup()
    except Exception as error:  # noqa: BLE001
        logger.warning(f"| ⚠️ sandbox cleanup: {error}")
    try:
        await trace_manager.stop()
    except Exception as error:  # noqa: BLE001
        logger.warning(f"| ⚠️ trace cleanup: {error}")


# How long the deployed-site server gets to finish in-flight requests at shutdown.
_TASK_STOP_SECONDS = 5.0


async def serve_deployed_site_names():
    """Answer `/s/<name>/` for this run's deployed sites, if nobody else is.

    A deployer asks for a free port each time it runs, so `http://host:PORT` names one
    deployment rather than one site: every redeploy hands out a different address and
    every link already given out stops working. `/s/<site>/` is the fix, and
    `/s/<site>--r<n>` reaches an older release from its archive — but both are routes on
    the gateway's app, and a headless run starts no gateway. So the addresses existed only
    for interactive sessions, and a run whose whole premise is asking people to return to
    a site they visited before handed each of them a different port every round.

    This serves the gateway's own app rather than a second copy of the route, because two
    servings of one address are two chances to disagree about it. If the port is already
    taken, a gateway is answering there and this run has nothing to add.
    """
    import socket

    from agentevolver.port import GATEWAY as GATEWAY_PORT

    host = "127.0.0.1"
    probe = socket.socket()
    try:
        probe.bind((host, GATEWAY_PORT))
    except OSError:
        # Something already listens there. Whatever it is owns the address, so leave
        # GATEWAY_PUBLIC_BASE to it rather than claiming an address this run cannot serve.
        logger.info(f"| 🌐 port {GATEWAY_PORT} is taken; leaving deployed-site names to it")
        return None
    finally:
        probe.close()

    try:
        import uvicorn
        from fastapi import FastAPI

        from agentevolver.gateway.transport import site_relay
    except Exception as exc:
        logger.warning(f"| 🌐 deployed sites stay port-addressed: {exc}")
        return None

    # Only the site relay, and the gateway's own definition of it. Serving the whole
    # gateway app would put an AgentGateway behind routes nobody started, and a second
    # copy of the route would be a second chance to disagree about what `/s/<name>/`
    # means. This run answers for deployed sites and nothing else.
    app = FastAPI(title="AgentEvolver deployed sites", version="1.0.0")
    app.include_router(site_relay)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=GATEWAY_PORT, log_level="warning"))
    task = asyncio.create_task(server.serve(), name="deployed-site-names")
    os.environ.setdefault("GATEWAY_PUBLIC_BASE", f"http://{host}:{GATEWAY_PORT}")
    logger.info(f"| 🌐 deployed sites are addressable at http://{host}:{GATEWAY_PORT}/s/<site>/")

    async def stop() -> None:
        """Ask the server to finish, rather than cancelling it mid-request.

        Cancelling uvicorn's `serve()` unwinds the ASGI lifespan through a
        CancelledError and prints a traceback at the end of every run that used this —
        an alarming way to report an ordinary shutdown.
        """
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=_TASK_STOP_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return stop


async def run_with_lifecycle() -> None:
    """Run the configured launcher and turn SIGINT/SIGTERM into graceful teardown."""
    loop = asyncio.get_running_loop()
    stop_requested = asyncio.Event()
    received: list[signal.Signals] = []

    def request_stop(sig: signal.Signals) -> None:
        received.append(sig)
        stop_requested.set()

    installed = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop, sig)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            pass

    stop_names = await serve_deployed_site_names()
    run_task = asyncio.create_task(main(), name="agent-launcher")
    signal_task = asyncio.create_task(stop_requested.wait(), name="launcher-stop-signal")
    try:
        done, _ = await asyncio.wait(
            {run_task, signal_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        if signal_task in done and not run_task.done():
            logger.warning(f"| 🛑 Received {received[-1].name}; stopping the complete run")
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        else:
            await run_task
    finally:
        signal_task.cancel()
        await asyncio.gather(signal_task, return_exceptions=True)
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        await teardown()
        if stop_names is not None:
            await stop_names()
        for sig in installed:
            loop.remove_signal_handler(sig)


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

    plugin_names = args.plugins if args.plugins is not None else getattr(config, "plugin_names", [])
    logger.info("| 🧩 Initializing plugins...")
    await plugin_manager.initialize(plugin_names=plugin_names)
    logger.info(f"| ✅ Plugins: {await plugin_manager.list()}")

    logger.info("| 🤖 Initializing agents...")
    await agent_manager.initialize(agent_names=config.agent_names)
    logger.info(f"| ✅ Agents: {await agent_manager.list()}")

    logger.info("| 🔀 Initializing workflows...")
    await workflow_manager.initialize(
        workflow_names=getattr(config, "workflow_names", [])
    )
    logger.info(f"| ✅ Workflows: {workflow_manager.list()}")

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
    try:
        while True:
            record = await task_manager.get(task_id)
            if record and record.task.status in (
                TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED
            ):
                break
            await asyncio.sleep(1)
    finally:
        answering_stop.set()
        answering.cancel()
        await asyncio.gather(answering, return_exceptions=True)

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

if __name__ == "__main__":
    asyncio.run(run_with_lifecycle())
