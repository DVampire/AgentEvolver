"""stdio and WebSocket adapters for :mod:`agentevolver.gateway.service`."""

from __future__ import annotations

import asyncio
import hmac
import json
import sys
from contextlib import suppress
from typing import Optional

from fastapi import APIRouter, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from agentevolver.gateway.types import GatewayCommand, error_response
from agentevolver.gateway.service import AgentGateway
from agentevolver.logger import logger


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
        # Released before the await, for the reason the websocket endpoint below
        # documents. This writer parks on a plain queue and does cancel cleanly, so
        # nothing is known to hang here — but a release that is only reached when a
        # background task agrees to stop is a release that can be skipped, and the two
        # paths should not differ on which of them is guaranteed.
        gateway.unsubscribe(event_queue)
        writer.cancel()
        with suppress(asyncio.CancelledError):
            await writer


#: How long a closing websocket waits for its background tasks before returning anyway.
#: A task parked in `send_text` on a gone socket may never finish being cancelled, and a
#: handler that waits for it holds its connection slot for the life of the process.
_TASK_SHUTDOWN_SECONDS = 2.0


# The same relay serves the full gateway and the detached sites-only entry point.
from agentevolver.gateway.sites import site_relay, deployed_site


def create_websocket_app(
    gateway: AgentGateway, *, token: Optional[str] = None,
    allowed_origins: Optional[set[str]] = None,
):
    """Create the optional FastAPI transport without importing it for stdio mode."""
    from fastapi.responses import JSONResponse

    app = FastAPI(title="AgentEvolver Gateway", version="1.0.0")

    @app.get("/health")
    async def health():
        return JSONResponse({"ok": True, "protocol_version": 1})

    @app.get("/ide/resolve/{session_id}")
    async def ide_resolve(session_id: str, port: int = 0):
        """Tell the UI's IDE proxy where a session's container port lives.

        ``/ide/<session>/`` on the UI's origin reaches the editor itself; the
        host form ``<port>-<session>.ide.localhost`` reaches any other port in
        that container, so a dev server or an OAuth callback listener started in
        the integrated terminal is reachable with no per-tool support. The Vite
        dev server matches either and asks here for the upstream to forward to.

        Bound to loopback like the rest of the gateway, and it returns nothing
        for an unknown session, so it cannot be used to probe other sessions.
        """
        from agentevolver.ide import ide_manager

        upstream = (
            await ide_manager.upstream_for_port(session_id, port) if port
            else ide_manager.upstream(session_id)
        )
        if not upstream:
            return JSONResponse({"ok": False, "error": "no IDE for this session"}, status_code=404)
        return JSONResponse({"ok": True, "upstream": upstream})

    @app.get("/science/resolve/{session_id}")
    async def science_resolve(session_id: str):
        """Tell the UI's proxy where a project's JupyterLab lives.

        A Jupyter Server in this container, on a loopback port of its own —
        the same server the agent's kernel runs in, which is what makes the Lab
        and the agent share variables. Unlike the IDE route this takes no
        ``port``: anything a notebook starts is reached through
        jupyter-server-proxy on that same one.
        """
        from agentevolver.kernel import kernel_manager

        upstream = kernel_manager.upstream(session_id)
        if not upstream:
            return JSONResponse({"ok": False, "error": "no workstation for this project"}, status_code=404)
        return JSONResponse({"ok": True, "upstream": upstream})

    def _authorize(websocket: WebSocket) -> bool:
        supplied_token = websocket.query_params.get("token") or websocket.headers.get("authorization", "").removeprefix("Bearer ")
        supplied_token = supplied_token or ""
        if token and not hmac.compare_digest(supplied_token, token):
            return False
        origin = websocket.headers.get("origin")
        if allowed_origins is not None and origin not in allowed_origins:
            return False
        return True

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        if not _authorize(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
            return
        await websocket.accept()
        event_queue = await gateway.subscribe()
        outbound: asyncio.Queue[str] = asyncio.Queue(maxsize=2_000)

        async def forward_events() -> None:
            while True:
                await outbound.put(_encode(await event_queue.get()))

        async def write_messages() -> None:
            while True:
                await websocket.send_text(await outbound.get())

        forwarder = asyncio.create_task(forward_events(), name="gateway-websocket-events")
        writer = asyncio.create_task(write_messages(), name="gateway-websocket-writer")
        try:
            while True:
                try:
                    command = GatewayCommand.model_validate_json(await websocket.receive_text())
                    response = await gateway.handle(command)
                except ValidationError as exc:
                    response = error_response("unknown", "invalid_command", str(exc))
                await outbound.put(_encode(response))
        except WebSocketDisconnect:
            pass
        finally:
            # Released first, and not behind the await below. Cancelling a task that is
            # parked inside `websocket.send_text` on a socket the client has already gone
            # from does not always complete: the cancellation is delivered and the task
            # never finishes, so `gather` waits forever and everything after it is dead
            # code. What that cost was the subscription — a queue the gateway keeps
            # feeding with nobody reading it, one per closed browser tab, which is the
            # unbounded growth this ordering exists to prevent.
            gateway.unsubscribe(event_queue)
            forwarder.cancel()
            writer.cancel()
            # Bounded for the same reason. Waiting is worth a moment so a task that can
            # stop does, but a handler that cannot return holds its connection slot.
            await asyncio.wait({forwarder, writer}, timeout=_TASK_SHUTDOWN_SECONDS)

    @app.websocket("/env/term/{token}/ws")
    async def terminal_ws(websocket: WebSocket, token: str):
        """Relay the terminal's websocket to the ttyd the ssh tunnel put on loopback."""
        if not _authorize(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
            return
        port = gateway._terminal_targets.get(token)
        # ttyd negotiates the "tty" subprotocol; echo it or the client refuses the socket.
        requested = websocket.headers.get("sec-websocket-protocol", "")
        protocols = [p.strip() for p in requested.split(",") if p.strip()]
        await websocket.accept(subprotocol=protocols[0] if protocols else None)
        if not port:
            await websocket.close(code=1011, reason="No such terminal")
            return

        import aiohttp

        async with aiohttp.ClientSession() as session:
            try:
                upstream = await session.ws_connect(
                    f"http://127.0.0.1:{port}/env/term/{token}/ws",
                    protocols=protocols or (), autoping=True,
                )
            except Exception:
                with suppress(Exception):
                    await websocket.close(code=1011, reason="Terminal unreachable")
                return

            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    if (data := message.get("bytes")) is not None:
                        await upstream.send_bytes(data)
                    elif (text := message.get("text")) is not None:
                        await upstream.send_str(text)

            async def upstream_to_client() -> None:
                async for msg in upstream:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        await websocket.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        await websocket.send_text(msg.data)

            tasks = [asyncio.create_task(client_to_upstream()),
                     asyncio.create_task(upstream_to_client())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            except WebSocketDisconnect:
                pass
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(Exception):
                    await upstream.close()

    @app.api_route("/env/term/{token}/{path:path}", methods=["GET", "POST"])
    async def terminal_http(request: Request, token: str, path: str):
        """Serve ttyd's page and assets from the gateway's own origin.

        ttyd runs with `-b /env/term/<token>`, so every URL it emits already carries this
        prefix and the paths line up on both sides without rewriting any HTML.
        """
        port = gateway._terminal_targets.get(token)
        if not port:
            return Response(status_code=404, content="No such terminal")

        import aiohttp

        target = f"http://127.0.0.1:{port}/env/term/{token}/{path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        body = await request.body()
        async with aiohttp.ClientSession() as session:
            try:
                async with session.request(
                    request.method, target, data=body or None,
                    headers={k: v for k, v in request.headers.items()
                             if k.lower() not in {"host", "connection", "content-length"}},
                    allow_redirects=False,
                ) as upstream:
                    payload = await upstream.read()
                    headers = {k: v for k, v in upstream.headers.items()
                               if k.lower() not in {"content-encoding", "content-length",
                                                    "transfer-encoding", "connection"}}
                    return Response(content=payload, status_code=upstream.status,
                                    headers=headers,
                                    media_type=upstream.headers.get("content-type"))
            except Exception:
                return Response(status_code=502, content="Terminal unreachable")

    app.include_router(site_relay)

    @app.websocket("/env/vnc/{env}")
    async def vnc_relay_for(websocket: WebSocket, env: str):
        """Relay one *named* environment's noVNC connection.

        The unqualified route below keeps a single most-recent target, which was fine
        while only the browser had a VNC view. With two such environments the second
        overwrote the first, so the view opened earlier connected to the other one's
        endpoint and went black. The name in the path is what keeps them apart.
        """
        await _vnc_bridge(websocket, gateway._vnc_targets.get(env))

    @app.websocket("/env/vnc")
    async def vnc_relay(websocket: WebSocket):
        await _vnc_bridge(websocket, gateway._latest_vnc_target)

    async def _vnc_bridge(websocket: WebSocket, target: Optional[str]) -> None:
        """Pipe frames between the browser and a sandbox websockify endpoint.

        That endpoint is reachable only on an ephemeral host port assigned by the
        opensandbox proxy. Rather than exposing it, the client connects here on the fixed
        gateway origin — so the remote experience stays one forwarded port (the UI).
        """
        if not _authorize(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
            return
        # noVNC negotiates a subprotocol ("binary"/"base64"); echo the client's
        # choice on accept and to the upstream, or the RFB handshake fails.
        requested = websocket.headers.get("sec-websocket-protocol", "")
        protocols = [p.strip() for p in requested.split(",") if p.strip()]
        subprotocol = protocols[0] if protocols else None
        await websocket.accept(subprotocol=subprotocol)
        if not target:
            await websocket.close(code=1011, reason="No live VNC target")
            return

        import aiohttp

        async with aiohttp.ClientSession() as session:
            try:
                upstream = await session.ws_connect(
                    target, protocols=protocols or (), autoping=True
                )
            except Exception as e:
                # Logged with the target: "upstream unreachable" alone says nothing about
                # WHICH endpoint failed or why, and the browser only ever shows a dead
                # canvas. The address is the first thing anyone debugging this needs.
                logger.warning(
                    f"| ⚠️ VNC relay could not reach {target}: {type(e).__name__}: {e}"
                )
                with suppress(Exception):
                    await websocket.close(code=1011, reason="VNC upstream unreachable")
                return

            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    if (data := message.get("bytes")) is not None:
                        await upstream.send_bytes(data)
                    elif (text := message.get("text")) is not None:
                        await upstream.send_str(text)

            async def upstream_to_client() -> None:
                async for msg in upstream:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        await websocket.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        await websocket.send_text(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        return

            c2u = asyncio.create_task(client_to_upstream(), name="vnc-c2u")
            u2c = asyncio.create_task(upstream_to_client(), name="vnc-u2c")
            try:
                await asyncio.wait({c2u, u2c}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                c2u.cancel()
                u2c.cancel()
                await asyncio.gather(c2u, u2c, return_exceptions=True)
                with suppress(Exception):
                    await upstream.close()
                with suppress(Exception):
                    await websocket.close()

    return app
