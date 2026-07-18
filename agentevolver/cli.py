"""Console entry point — ``agentevolver /<command> [args]``.

Installed as the ``agentevolver`` console script. Bootstraps the managers a control
command needs, then dispatches one ``/command`` and prints the result. This is the
standalone command-line interface to the framework (no agent loop involved).

Bootstrap is resilient: ``/help`` needs no managers at all, and every capability manager
is initialized under a timeout so a slow/unavailable subsystem (e.g. a browser
environment or an MCP connector) degrades to "skipped" instead of hanging the CLI.

Examples:
    agentevolver /help
    agentevolver /registry
    agentevolver /checkpoint pre-evolve
    agentevolver /rollback tool bash_tool 1.0.0
    agentevolver --config configs/meta_agent.py /registry
"""
import sys
import asyncio
import argparse
from argparse import Namespace

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.version import version_manager
from agentevolver.prompt import prompt_manager
from agentevolver.tool import tool_manager
from agentevolver.skill import skill_manager
from agentevolver.connector import connector_manager
from agentevolver.environment import environment_manager
from agentevolver.agent import agent_manager
from agentevolver.command import command_manager


async def _try(name: str, coro, timeout: float):
    """Run a manager init under a timeout; on failure, warn and continue (never hang)."""
    try:
        await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        print(f"[bootstrap] {name} init timed out after {timeout:.0f}s — skipped", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[bootstrap] {name} init failed ({e}) — skipped", file=sys.stderr)


async def _bootstrap(config_path: str, timeout: float = 60.0):
    """Initialize the managers a control command may touch, each bounded by a timeout."""
    config.initialize(config_path=config_path, args=Namespace(cfg_options=None), verbose=False)
    logger.initialize(config=config)
    await _try("version", version_manager.initialize(), timeout)
    await _try("prompt", prompt_manager.initialize(prompt_names=getattr(config, "prompt_names", None)), timeout)
    await _try("tool", tool_manager.initialize(tool_names=getattr(config, "tool_names", None)), timeout)
    await _try("skill", skill_manager.initialize(skill_names=getattr(config, "skill_names", None)), timeout)
    await _try("connector", connector_manager.initialize(connector_names=getattr(config, "connector_names", None)), timeout)
    await _try("environment", environment_manager.initialize(env_names=getattr(config, "environment_names", None)), timeout)
    await _try("agent", agent_manager.initialize(agent_names=getattr(config, "agent_names", None)), timeout)
    await command_manager.initialize()


async def _run(raw: str, config_path: str) -> int:
    head = raw.strip().lstrip("/").split()[:1]
    if head and head[0] in ("help", "?"):
        # /help lists the command registry — no capability bootstrap needed.
        await command_manager.initialize()
    else:
        await _bootstrap(config_path)
    resp = await command_manager.dispatch(raw)
    print(("✅ " if resp.success else "❌ ") + raw)
    print(resp.message)
    return 0 if resp.success else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="agentevolver", description="AgentEvolver control commands")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="the command line, e.g. /registry")
    parser.add_argument("--config",
                        help="config file (determines which capabilities are registered).")
    args = parser.parse_args()
    if not args.command:
        print("usage: agentevolver /<command> [args]   (try: agentevolver /help)")
        return 0
    if args.command[0] == "serve":
        from agentevolver.gateway.cli import main as gateway_main
        prefix = ["--config", args.config] if args.config else []
        return gateway_main([*prefix, *args.command[1:]])
    return asyncio.run(_run(" ".join(args.command), args.config or "configs/base.py"))


if __name__ == "__main__":
    raise SystemExit(main())
