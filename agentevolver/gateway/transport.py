"""stdio and WebSocket adapters for :mod:`agentevolver.gateway.service`."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import suppress
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from agentevolver.gateway.protocol import GatewayCommand, error_response
from agentevolver.gateway.service import AgentGateway


def _encode(message) -> str:
    return message.model_dump_json()


async def serve_stdio(gateway: AgentGateway) -> None:
    """Serve JSON Lines over standard streams without mixing in runtime logs."""
    event_queue = await gateway.subscribe()

    async def write_events() -> None:
        while True:
            message = await event_queue.get()
            print(_encode(message), flush=True)

    writer = asyncio.create_task(write_events(), name="gateway-stdio-events")
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            try:
                raw = json.loads(line)
                command = GatewayCommand.model_validate(raw)
                response = await gateway.handle(command)
            except json.JSONDecodeError as exc:
                response = error_response("unknown", "invalid_json", str(exc))
            except ValidationError as exc:
                response = error_response("unknown", "invalid_command", str(exc))
            print(_encode(response), flush=True)
    finally:
        writer.cancel()
        with suppress(asyncio.CancelledError):
            await writer
        gateway.unsubscribe(event_queue)


def create_websocket_app(gateway: AgentGateway, *, token: Optional[str] = None):
    """Create the optional FastAPI transport without importing it for stdio mode."""
    from fastapi.responses import JSONResponse

    app = FastAPI(title="AgentEvolver Gateway", version="1.0.0")

    @app.get("/health")
    async def health():
        return JSONResponse({"ok": True, "protocol_version": 1})

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        supplied_token = websocket.query_params.get("token") or websocket.headers.get("authorization", "").removeprefix("Bearer ")
        if token and supplied_token != token:
            await websocket.close(code=1008, reason="Invalid gateway token")
            return
        await websocket.accept()
        event_queue = await gateway.subscribe()

        async def write_events() -> None:
            while True:
                await websocket.send_text(_encode(await event_queue.get()))

        writer = asyncio.create_task(write_events(), name="gateway-websocket-events")
        try:
            while True:
                try:
                    command = GatewayCommand.model_validate_json(await websocket.receive_text())
                    response = await gateway.handle(command)
                except ValidationError as exc:
                    response = error_response("unknown", "invalid_command", str(exc))
                await websocket.send_text(_encode(response))
        except WebSocketDisconnect:
            pass
        finally:
            writer.cancel()
            with suppress(asyncio.CancelledError):
                await writer
            gateway.unsubscribe(event_queue)

    return app
