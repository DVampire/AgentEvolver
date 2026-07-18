"""`agentevolver serve` command implementation."""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional, Sequence

from agentevolver.gateway.service import AgentGateway
from agentevolver.gateway.transport import create_websocket_app, serve_stdio


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentevolver serve", description="Run the interactive AgentEvolver Gateway")
    parser.add_argument("--config", default="configs/meta_agent.py")
    parser.add_argument("--transport", choices=("stdio", "websocket"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--token", default=os.getenv("AGENTEVOLVER_GATEWAY_TOKEN"))
    return parser


async def _run_stdio(args) -> int:
    gateway = AgentGateway()
    await gateway.start(args.config, stdio=True)
    try:
        await serve_stdio(gateway)
    finally:
        await gateway.stop()
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.transport == "stdio":
        return asyncio.run(_run_stdio(args))

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
