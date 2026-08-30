"""Run SSHAgent against a remote machine through the SSH environment.

The host is not hardcoded and has no default: point it at a machine you already reach with
`ssh`, either on the command line or in the config.

    python examples/run_ssh_agent.py --host gpu-box --remote-root '~/project' \
        --task "check GPU occupancy and report which cards are free"

With no `--task` it runs `examples/tasks/remote_workspace_audit.html`, which audits the
remote workspace as a background job and brings the report back.

`--host` accepts anything ssh accepts, including a `~/.ssh/config` alias — in which case
user, port, identity and jump host all come from there and need not be repeated.
"""
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
from agentevolver.paths import P, path_manager
from agentevolver.logger import logger
from agentevolver.model import model_manager
from agentevolver.version import version_manager
from agentevolver.prompt import prompt_manager
from agentevolver.memory import memory_manager
from agentevolver.tool import tool_manager
from agentevolver.skill import skill_manager
from agentevolver.agent import agent_manager
from agentevolver.environment import environment_manager
from agentevolver.hook import hook_manager
from agentevolver.task import task_manager, TaskCategory, TaskPriority, TaskRecord, TaskStatus, add_task_args, resolve_task
from agentevolver.trace import trace_manager
from agentevolver.trajectory import trajectory_manager
from agentevolver.session.project import bind_session_roots, ensure_session_sandbox
from agentevolver.session.types import SessionContext
from agentevolver.utils import make_id


def parse_args():
    parser = argparse.ArgumentParser(description="Run SSHAgent against a remote machine")
    parser.add_argument(
        "--config",
        default=os.path.join(root, "configs", "ssh_agent.py"),
        help="Config file path",
    )
    # Connection details on the command line, because the host is the one thing that
    # changes per run. Each of these overrides the config's `remote_host` block; leaving
    # one out keeps whatever the config says.
    parser.add_argument("--host", default="", help="Hostname or ~/.ssh/config alias")
    parser.add_argument("--user", default="", help="Remote user (blank → ssh resolves it)")
    parser.add_argument("--port", type=int, default=0, help="SSH port")
    parser.add_argument("--identity-file", default="", help="Private key path")
    parser.add_argument("--jump-host", default="", help="Bastion to reach the host through")
    # Deliberately not `--workspace-root`: config.initialize folds argparse attributes
    # into the config, where `workspace_root` is the *local* run workspace and is
    # validated to sit under project_root. A remote path there fails that check.
    parser.add_argument(
        "--remote-root",
        default="",
        help="Directory on the remote host the agent is confined to",
    )
    parser.add_argument(
        "--no-live-view",
        action="store_true",
        help="Do not start the read-only terminal view on the remote host",
    )
    add_task_args(parser, default_task_file=os.path.join(root, "examples", "tasks", "remote_workspace_audit.html"))
    parser.add_argument(
        "--cfg-options",
        nargs="+",
        action=DictAction,
        help="Override config options in key=value format",
    )
    return parser.parse_args()


def apply_connection_args(args) -> None:
    """Fold the command-line connection flags into the config's `remote_host` block."""
    overrides = {
        "host": args.host,
        "user": args.user,
        "identity_file": args.identity_file,
        "jump_host": args.jump_host,
        "workspace_root": args.remote_root,
    }
    block = config.remote_host
    for key, value in overrides.items():
        if value:
            block[key] = value
    if args.port:
        block["port"] = args.port
    if args.no_live_view:
        block["live_view"] = False

    if not block.get("host"):
        raise SystemExit(
            "No remote host given. Pass --host (a hostname or a ~/.ssh/config alias), "
            "or set `host` in the config's remote_host block."
        )


async def run_agent(record: TaskRecord, ctx: SessionContext):
    """TaskManager handler: executes the ssh agent for a given TaskRecord."""
    response = await agent_manager(
        name="ssh_agent",
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
    apply_connection_args(args)

    # A direct script is a session too. Binding the roots here — before any manager
    # initializes — is what gives the agent a local workspace that actually exists: the
    # config's pre-binding default is a path nothing ever creates, so `bash_tool` and any
    # `download` destination would both point at a missing directory.
    session_id = make_id()
    ctx = SessionContext(id=session_id, name="main_entrypoint")
    sandbox = ensure_session_sandbox(ctx, shared_extension_root=config.extension_root)
    bind_session_roots(config, sandbox)

    logger.initialize(config=config)
    logger.info(f"| 🗂️  Session {session_id} → {config.workspace_root}")
    logger.info(f"| 🖥️  Remote host: {config.remote_host['host']} "
                f"(workspace {config.remote_host['workspace_root']})")

    logger.info("| 📁 Initializing version manager...")
    await version_manager.initialize()

    trace_log_root = str(path_manager.under(config.log_root, P.LOG_MODULE, module="trace"))
    await trace_manager.initialize(log_root=trace_log_root)
    await trace_manager.start()
    # `hook_manager.initialize()` registers the trajectory hook, so the manager it writes
    # through has to be pointed somewhere before the first action.
    await trajectory_manager.initialize()

    await hook_manager.initialize()

    logger.info("| 🧠 Initializing model manager...")
    await model_manager.initialize()
    logger.info(f"| ✅ Models: {model_manager.list()}")

    logger.info("| 📁 Initializing prompt manager...")
    await prompt_manager.initialize()

    logger.info("| 📁 Initializing memory manager...")
    await memory_manager.initialize(memory_names=config.memory_names)

    logger.info("| 🛠️ Initializing tools...")
    await tool_manager.initialize(tool_names=config.tool_names)
    logger.info(f"| ✅ Tools: {await tool_manager.list()}")

    logger.info("| 🎯 Initializing skills...")
    await skill_manager.initialize(skill_names=getattr(config, "skill_names", None))

    logger.info("| 🌐 Initializing environments...")
    await environment_manager.initialize(env_names=config.env_names)
    logger.info(f"| ✅ Environments: {await environment_manager.list()}")

    logger.info("| 🤖 Initializing agents...")
    await agent_manager.initialize(agent_names=config.agent_names)
    logger.info(f"| ✅ Agents: {await agent_manager.list()}")

    logger.info(f"| 📋 All versions: {json.dumps(await version_manager.list(), indent=4)}")

    task_log_root = str(path_manager.under(config.log_root, P.LOG_MODULE, module="tasks"))
    await task_manager.initialize(log_root=task_log_root, handler=lambda record: run_agent(record, ctx))
    await task_manager.start(num_workers=1)

    task_text, task_files, task_metadata = resolve_task(
        args, task_log_root,
        default_text=(
            "Report what this machine is: its hostname, how many GPUs it has and how "
            "many are currently free, and how much disk space the workspace has left."
        ),
    )
    logger.info(f"| 📋 Submitting task: {task_text}")
    task_id = await task_manager.submit(
        content=task_text,
        category=TaskCategory.USER,
        priority=TaskPriority.HIGH,
        files=task_files,
        metadata=task_metadata,
    )

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

    await task_manager.stop()
    # Takes the connection and the view down with it. Jobs started via `launch` are
    # deliberately left running — that is what launching one means.
    await environment_manager.cleanup()
    await trace_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
