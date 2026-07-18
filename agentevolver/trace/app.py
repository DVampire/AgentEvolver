"""FastAPI application for the Trace web UI.

Endpoints
---------
GET  /                           → serve React SPA (index.html)
GET  /api/sessions               → list all traced sessions
GET  /api/sessions/{session_id}  → all events for a session (JSON array)
WS   /ws/live                    → real-time event stream (all sessions)
WS   /ws/session/{session_id}    → real-time stream filtered to one session
GET  /static/*                   → React build assets

The WebSocket broadcaster is wired into the EventQueue via a separate
asyncio task that fans out each event to all connected WebSocket clients.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional, Set

from agentevolver.trace.writer import TraceWriter
from agentevolver.trace.types import TraceEvent

# Deferred imports — fastapi/starlette are optional
def build_app(writer: Optional[TraceWriter] = None):
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware import Middleware

    app = FastAPI(
        title="AgentEvolver Trace UI",
        version="1.0.0",
        middleware=[
            Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
        ],
    )

    # ------------------------------------------------------------------
    # WebSocket connection manager
    # ------------------------------------------------------------------

    class ConnectionManager:
        def __init__(self):
            self._all: Set[WebSocket] = set()
            self._session: dict[str, Set[WebSocket]] = {}
            self._lock = asyncio.Lock()

        async def connect_all(self, ws: WebSocket) -> None:
            await ws.accept()
            async with self._lock:
                self._all.add(ws)

        async def connect_session(self, ws: WebSocket, session_id: str) -> None:
            await ws.accept()
            async with self._lock:
                self._session.setdefault(session_id, set()).add(ws)

        async def disconnect(self, ws: WebSocket, session_id: Optional[str] = None) -> None:
            async with self._lock:
                self._all.discard(ws)
                if session_id and session_id in self._session:
                    self._session[session_id].discard(ws)

        async def broadcast(self, event: TraceEvent) -> None:
            payload = json.dumps(event.to_dict(), ensure_ascii=False)
            dead: list = []

            # Broadcast to "all" subscribers
            for ws in list(self._all):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append((ws, None))

            # Broadcast to session-specific subscribers
            sid = event.session_id or ""
            for ws in list(self._session.get(sid, [])):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append((ws, sid))

            for ws, sid in dead:
                await self.disconnect(ws, sid)

    manager = ConnectionManager()

    # ------------------------------------------------------------------
    # Broadcast pump: drains a secondary queue so the main writer queue
    # is not shared.  TraceManager registers this callback.
    # ------------------------------------------------------------------

    broadcast_callbacks = []

    async def _broadcast_event(event: TraceEvent) -> None:
        await manager.broadcast(event)

    broadcast_callbacks.append(_broadcast_event)

    # Attach to app so TraceManager can register its queue drain
    app.state.broadcast_callbacks = broadcast_callbacks

    # ------------------------------------------------------------------
    # REST endpoints
    # ------------------------------------------------------------------

    @app.get("/api/sessions")
    async def list_sessions():
        if writer is None:
            return JSONResponse([])
        return JSONResponse(writer.list_sessions())

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        if writer is None:
            return JSONResponse([])
        events = writer.read_session(session_id)
        return JSONResponse(events)

    # ------------------------------------------------------------------
    # WebSocket endpoints
    # ------------------------------------------------------------------

    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket):
        await manager.connect_all(websocket)
        try:
            while True:
                # Keep the connection alive; events are pushed server→client
                await asyncio.sleep(30)
                await websocket.send_text(json.dumps({"type": "ping"}))
        except (WebSocketDisconnect, Exception):
            await manager.disconnect(websocket)

    @app.websocket("/ws/session/{session_id}")
    async def ws_session(websocket: WebSocket, session_id: str):
        await manager.connect_session(websocket, session_id)
        # On connect, replay existing events for this session
        if writer:
            history = writer.read_session(session_id)
            for ev_dict in history:
                try:
                    await websocket.send_text(
                        json.dumps({**ev_dict, "_replay": True}, ensure_ascii=False)
                    )
                except Exception:
                    break
        try:
            while True:
                await asyncio.sleep(30)
                await websocket.send_text(json.dumps({"type": "ping"}))
        except (WebSocketDisconnect, Exception):
            await manager.disconnect(websocket, session_id)

    # ------------------------------------------------------------------
    # Static files — React build
    # ------------------------------------------------------------------

    _ui_dir = os.path.join(os.path.dirname(__file__), "ui", "dist")
    _ui_index = os.path.join(_ui_dir, "index.html")

    if os.path.isdir(_ui_dir):
        # Explicit routes registered BEFORE the static mount so /api/* wins
        @app.get("/")
        async def serve_index():
            return FileResponse(_ui_index)

        # Mount the whole dist/ as static — handles /assets/* and any other
        # build artefacts without a catch-all route that would shadow /api/*
        app.mount("/", StaticFiles(directory=_ui_dir, html=True), name="spa")
    else:
        @app.get("/")
        async def no_ui():
            return JSONResponse({
                "message": "React UI not built yet.",
                "instructions": "cd agentevolver/trace/ui && npm install && npm run build",
            })

    return app, manager
