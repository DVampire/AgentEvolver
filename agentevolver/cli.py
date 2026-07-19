"""The single console entry point for AgentEvolver.

The ``agentevolver`` command owns all user-facing modes: a one-shot control command,
the interactive terminal loop (``tui``), and the Gateway service (``serve``).

Gateway code deliberately exposes no separate console entry point: it is a runtime
transport selected by this module, not a competing application CLI.

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
import asyncio
import argparse
import os
import sys
from argparse import Namespace
from contextlib import asynccontextmanager
from typing import Optional, Sequence

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
    from agentevolver.agent import agent_manager
    from agentevolver.command import command_manager
    from agentevolver.config import config
    from agentevolver.connector import connector_manager
    from agentevolver.environment import environment_manager
    from agentevolver.logger import logger
    from agentevolver.prompt import prompt_manager
    from agentevolver.skill import skill_manager
    from agentevolver.tool import tool_manager
    from agentevolver.version import version_manager

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
    from agentevolver.command import command_manager

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


async def _run_tui(config_path: str) -> int:
    """Run a small terminal command loop over the command registry."""
    from agentevolver.command import command_manager

    print("AgentEvolver terminal mode. Type /help for commands; /exit to quit.")
    bootstrapped = False
    await command_manager.initialize()
    while True:
        try:
            raw = input("agentevolver> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw:
            continue
        if raw.lstrip("/").lower() in {"exit", "quit"}:
            return 0
        head = raw.lstrip("/").split()[:1]
        if not (head and head[0] in ("help", "?")) and not bootstrapped:
            await _bootstrap(config_path)
            bootstrapped = True
        resp = await command_manager.dispatch(raw)
        print(("✅ " if resp.success else "❌ ") + resp.message)


def _control_main(argv: Optional[Sequence[str]] = None) -> int:
    """Run a single control command, preserving the historic CLI interface."""
    parser = argparse.ArgumentParser(prog="agentevolver", description="AgentEvolver control commands")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="the command line, e.g. /registry")
    parser.add_argument("--config",
                        help="config file (determines which capabilities are registered).")
    args = parser.parse_args(argv)
    if not args.command:
        print("usage: agentevolver /<command> [args]   (try: agentevolver /help)")
        return 0
    return asyncio.run(_run(" ".join(args.command), args.config or "configs/base.py"))


def _tui_main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agentevolver tui", description="Run the interactive terminal interface")
    parser.add_argument("--config", default="configs/base.py", help="config file for registered capabilities")
    args = parser.parse_args(argv)
    return asyncio.run(_run_tui(args.config))


async def _run_gateway_stdio(config_path: str) -> int:
    from agentevolver.gateway.service import AgentGateway
    from agentevolver.gateway.transport import serve_stdio

    gateway = AgentGateway()
    await gateway.start(config_path, stdio=True)
    try:
        await serve_stdio(gateway)
    finally:
        await gateway.stop()
    return 0


def gateway_main(argv: Optional[Sequence[str]] = None) -> int:
    """Start the Gateway runtime; called by the top-level ``serve`` mode."""
    parser = argparse.ArgumentParser(prog="agentevolver serve", description="Run the interactive AgentEvolver Gateway")
    parser.add_argument("--config", default="configs/meta_agent.py")
    parser.add_argument("--transport", choices=("stdio", "websocket"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--token", default=os.getenv("AGENTEVOLVER_GATEWAY_TOKEN"))
    args = parser.parse_args(argv)
    if args.transport == "stdio":
        return asyncio.run(_run_gateway_stdio(args.config))

    from agentevolver.gateway.service import AgentGateway
    from agentevolver.gateway.transport import create_websocket_app
    import uvicorn

    gateway = AgentGateway()

    @asynccontextmanager
    async def lifespan(app):
        await gateway.start(args.config)
        try:
            yield
        finally:
            await gateway.stop()

    app = create_websocket_app(gateway, token=args.token)
    app.router.lifespan_context = lifespan
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _mode_and_remainder(argv: Sequence[str]) -> tuple[Optional[str], list[str]]:
    """Find the first positional mode while accepting ``--config`` before it."""
    values = list(argv)
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--config":
            index += 2
            continue
        if value.startswith("--config="):
            index += 1
            continue
        if not value.startswith("-"):
            return value, [*values[:index], *values[index + 1:]]
        index += 1
    return None, values


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Route control commands, terminal mode, and the Gateway from one CLI."""
    values = list(sys.argv[1:] if argv is None else argv)
    mode, remainder = _mode_and_remainder(values)
    if mode == "serve":
        return gateway_main(remainder)
    if mode == "tui":
        return _tui_main(remainder)
    return _control_main(values)


if __name__ == "__main__":
    raise SystemExit(main())
