"""The single console entry point for AgentEvolver.

The ``agentevolver`` command owns all user-facing modes: a one-shot control command,
the interactive terminal loop (``tui``), and the Gateway service (``serve``).

The Gateway is the single backend: it owns every capability manager's lifecycle.
The one-shot command and the terminal loop (``tui``) do NOT bootstrap managers
themselves — they drive an in-process Gateway (``session.create`` +
``command.execute``), so there is exactly one place that initializes
capabilities and one command-dispatch path. ``serve`` runs the same Gateway over
a stdio/websocket transport for remote clients (web UI, terminal client).

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
import ipaddress
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Sequence

from agentevolver.port import GATEWAY as GATEWAY_PORT


@asynccontextmanager
async def _gateway_session(config_path: str):
    """Drive the Gateway in-process.

    The Gateway is the single backend that owns every manager's lifecycle, so
    control commands and the terminal loop go THROUGH it instead of a second,
    parallel bootstrap — one place initializes capabilities, one command
    dispatch. Yields ``(gateway, session_id)`` and stops the Gateway on exit.
    """
    from agentevolver.gateway.protocol import GatewayCommand
    from agentevolver.gateway.service import AgentGateway
    from agentevolver.utils import make_id

    gateway = AgentGateway(workspace_source=Path.cwd())
    await gateway.start(config_path, stdio=True)
    try:
        created = await gateway.handle(GatewayCommand(id=make_id(), method="session.create"))
        if not created.ok:
            raise RuntimeError(created.error.message if created.error else "session.create failed")
        yield gateway, created.result["session_id"]
    finally:
        await gateway.stop()


async def _dispatch(gateway, session_id: str, raw: str) -> tuple[bool, str]:
    """Run one slash command through the Gateway's ``command.execute``."""
    from agentevolver.gateway.protocol import GatewayCommand
    from agentevolver.utils import make_id

    resp = await gateway.handle(GatewayCommand(
        id=make_id(), method="command.execute",
        params={"session_id": session_id, "raw": raw},
    ))
    if not resp.ok:
        return False, resp.error.message if resp.error else "command failed"
    return bool(resp.result.get("success")), str(resp.result.get("message", ""))


async def _run(raw: str, config_path: str) -> int:
    """Run a single control command through the Gateway."""
    async with _gateway_session(config_path) as (gateway, session_id):
        ok, message = await _dispatch(gateway, session_id, raw)
    print(("✅ " if ok else "❌ ") + raw)
    print(message)
    return 0 if ok else 1


async def _run_tui(config_path: str) -> int:
    """Run the interactive terminal loop over the Gateway."""
    print("AgentEvolver terminal mode. Type /help for commands; /exit to quit.")
    async with _gateway_session(config_path) as (gateway, session_id):
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
            ok, message = await _dispatch(gateway, session_id, raw)
            print(("✅ " if ok else "❌ ") + message)


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
    parser.add_argument("--config", default="configs/meta_agent.py", help="config file for registered capabilities")
    args = parser.parse_args(argv)
    return asyncio.run(_run_tui(args.config))


async def _run_gateway_stdio(config_path: str) -> int:
    from agentevolver.gateway.service import AgentGateway
    from agentevolver.gateway.transport import serve_stdio

    gateway = AgentGateway(workspace_source=Path.cwd())
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
    parser.add_argument("--port", type=int, default=GATEWAY_PORT)
    parser.add_argument("--token", default=os.getenv("AGENTEVOLVER_GATEWAY_TOKEN"))
    parser.add_argument(
        "--allow-origin", action="append", default=None,
        help="Allowed WebSocket Origin; repeat for multiple browser origins",
    )
    args = parser.parse_args(argv)
    if args.transport == "stdio":
        return asyncio.run(_run_gateway_stdio(args.config))
    try:
        is_loopback = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        is_loopback = args.host == "localhost"
    if not is_loopback and not args.token:
        parser.error("--token (or AGENTEVOLVER_GATEWAY_TOKEN) is required for non-loopback hosts")

    from agentevolver.gateway.service import AgentGateway
    from agentevolver.gateway.transport import create_websocket_app
    import uvicorn

    gateway = AgentGateway(workspace_source=Path.cwd())

    @asynccontextmanager
    async def lifespan(app):
        await gateway.start(args.config)
        try:
            yield
        finally:
            await gateway.stop()

    app = create_websocket_app(
        gateway, token=args.token,
        allowed_origins=set(args.allow_origin) if args.allow_origin else None,
    )
    app.router.lifespan_context = lifespan
    from agentevolver.port import port_manager
    port_manager.register("gateway", args.port, kind="host")
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
