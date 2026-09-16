"""Persistent, loopback-only entry point for registered deployments.

Applications serve at their internal root and generate browser URLs using
BASE_PATH / X-Forwarded-Prefix. Arbitrary JavaScript is never rewritten here.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import os
import re
import subprocess
import sys
from urllib.parse import quote, urlsplit

import aiohttp
from fastapi import APIRouter, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, RedirectResponse, StreamingResponse
from starlette.responses import FileResponse

from agentevolver.deploy import deployment_manager
from agentevolver.deploy.types import DeployRequest, SiteStatus
from agentevolver.logger import logger
from agentevolver.port import GATEWAY

site_relay = APIRouter()
#: One rebuild at a time per site; concurrent visitors to a dead address share it.
_reviving: dict[str, asyncio.Lock] = {}
_UPSTREAM_HEADERS_TIMEOUT_S = 30.0
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length"}


def headers_to_upstream(headers, prefix):
    blocked = _HOP | {v.strip().lower() for v in headers.get("connection", "").split(",")}
    result = {k: v for k, v in headers.items()
              if k.lower() not in blocked and not k.lower().startswith("x-forwarded-")}
    result["X-Forwarded-Prefix"] = prefix
    result["X-Forwarded-Host"] = headers.get("host", "")
    return result


async def _backend_alive(url: str) -> bool:
    """Whether a loopback backend still answers. Remote addresses are not ours to judge."""
    parts = urlsplit(url)
    if parts.hostname not in {"127.0.0.1", "localhost", "::1"} or not parts.port:
        return True
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(parts.hostname, parts.port), timeout=0.3
        )
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    with suppress(Exception):
        await writer.wait_closed()
    return True


async def _revive(name: str) -> None:
    """Rebuild a site whose record says RUNNING but whose port answers nothing.

    A deployment outlives its own process more often than the record admits: the status
    was written when the health check passed and nothing revisits it, so the address stays
    registered, the log stays empty, and every later visit gets a 502 that explains
    nothing. Rebuilding from the stored request is exactly what a person does by hand;
    doing it here means the first visitor after a death pays a rebuild instead of finding
    a dead page. The port changes, the name does not, so no published address breaks.
    """
    # Follow the record that supplied the URL, including a pinned archive's own
    # backend. Rebuilding its base would leave the broken archive untouched and
    # interrupt unrelated readers of the latest release.
    record = deployment_manager._serving_record(name) or deployment_manager._sites.get(name)
    if record is None:
        split = deployment_manager._split_release(name)
        candidate = deployment_manager._sites.get(split[0]) if split else None
        if candidate is not None and candidate.release_number == split[1]:
            record = candidate
    if record is None or not record.request:
        return
    site_id = record.site_id
    lock = _reviving.setdefault(site_id, asyncio.Lock())
    if lock.locked():
        async with lock:  # another visitor is already rebuilding it; take their result
            return
    async with lock:
        logger.warning(f"| ⚠️ Site {site_id!r} is registered but unreachable; rebuilding it")
        try:
            # Deploy from the preserved recipe/archive. The manager verifies old
            # resource ownership before replacement; a dead port alone is not proof
            # that an unknown backend process may be killed.
            await deployment_manager.deploy(deployment_manager.restoration_request(record))
            logger.info(f"| ♻️ Site {site_id!r} rebuilt after an unreachable backend")
        except Exception as error:  # noqa: BLE001 - a failed revival still answers 502
            logger.warning(f"| ⚠️ Could not rebuild site {site_id!r}: {error}")


def _revivable(name: str) -> bool:
    """Whether a name has a record the deployer can rebuild from.

    DETACHED is the deployer's own word for "the registry entry outlived its process",
    and its docstring already says a stored request can bring one back. Nothing ever
    asked it to, so a gateway restart turned every previously working address into a
    404 that only a human could fix.
    """
    split = deployment_manager._split_release(name)
    record = deployment_manager._sites.get(split[0] if split else name)
    return bool(record is not None and record.request
                and record.status == SiteStatus.DETACHED)


async def site_target(name):
    deployment_manager.refresh()
    if deployment_manager._split_release(name):
        await deployment_manager.ensure_release(name)
    url = deployment_manager.resolve_url(name)
    # Two ways an address goes dead, one answer: the record still claims a live backend
    # that answers nothing, or the record already knows its process is gone. Either way
    # the stored request can rebuild it, and the visitor waits for a rebuild instead of
    # reading an error that names no cause.
    if (url and not await _backend_alive(url)) or (not url and _revivable(name)):
        await _revive(name)
        deployment_manager.refresh()
        url = deployment_manager.resolve_url(name)
    return url if url and urlsplit(url).scheme in {"http", "https"} else None


def target_url(base, path, query):
    # Re-encode decoded ASGI paths; user text must never replace the loopback authority.
    url = urlsplit(base)
    query = "&".join(part for part in (url.query, query) if part)
    return url._replace(path=url.path.rstrip("/") + "/" + quote(path, safe="/@:+,;=-._~"), query=query, fragment="").geturl()


@site_relay.get("/_sites/health")
async def site_health():
    return {"service": "agentevolver-sites", "schema": 1}


@site_relay.get("/_sites/api/pages")
async def site_pages():
    return JSONResponse(deployment_manager.public_pages(), headers={"Cache-Control": "no-store"})


@site_relay.get("/sites/")
async def site_index():
    from agentevolver.paths import path_manager
    return FileResponse(path_manager.package_resource("visual", "sites", "index.html"),
                        headers={"Content-Security-Policy": "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})


@site_relay.get("/_sites/assets/{name}")
async def site_asset(name: str):
    from agentevolver.paths import path_manager
    assets = {"style.css": ("benchmark", "style.css"), "app.js": ("sites", "app.js"),
              "pages.css": ("sites", "style.css")}
    if name not in assets:
        return Response(status_code=404)
    return FileResponse(path_manager.package_resource("visual", *assets[name]))


@site_relay.api_route("/s/{name}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def site_slash(request: Request, name: str):
    return RedirectResponse(str(request.url.replace(path=request.url.path + "/")), status_code=307)


@site_relay.api_route("/s/{name}/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def deployed_site(request: Request, name: str, path: str = ""):
    target = await site_target(name)
    if not target:
        record = deployment_manager._sites.get(name)
        if record is not None and record.status in {SiteStatus.FAILED, SiteStatus.BUILDING, SiteStatus.DETACHED}:
            return Response(
                "Deployment is unavailable: the service could not start or is still starting. "
                "Check its deployment status and server log in the monitor.",
                status_code=503, media_type="text/plain",
                headers={"Retry-After": "30", "Cache-Control": "no-store"},
            )
        return Response("Site is not running", status_code=404)
    prefix = f"/s/{quote(name, safe='')}/"
    client = aiohttp.ClientSession(auto_decompress=False, timeout=aiohttp.ClientTimeout(total=None, sock_connect=10))
    try:
        headers = headers_to_upstream(request.headers, prefix)
        headers["X-Forwarded-Proto"] = request.url.scheme
        # The ASGI stream otherwise becomes chunked, which stdlib HTTP servers
        # cannot decode. Preserve known lengths; bound buffering for unknown ones.
        length = request.headers.get("content-length")
        data = request.stream()
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                await client.close()
                return Response("Invalid Content-Length", status_code=400)
            headers["Content-Length"] = str(int(length))
        else:
            body_bytes = bytearray()
            async for chunk in request.stream():
                if len(body_bytes) + len(chunk) > 16 * 1024 * 1024:
                    await client.close()
                    return Response("Provide Content-Length for uploads over 16 MiB", status_code=413)
                body_bytes.extend(chunk)
            data = bytes(body_bytes)
            headers["Content-Length"] = str(len(data))
        # A listening TCP socket need not ever send HTTP headers. Bound that wait
        # without imposing a total lifetime on SSE or streamed downloads.
        upstream = await asyncio.wait_for(
            client.request(request.method, target_url(target, path, request.url.query),
                           data=data, headers=headers, allow_redirects=False),
            timeout=_UPSTREAM_HEADERS_TIMEOUT_S,
        )
    except BaseException as error:
        await client.close()
        if isinstance(error, TimeoutError):
            return Response(
                "The deployed service did not respond in time. Retry or check its deployment log.",
                status_code=504, media_type="text/plain", headers={"Cache-Control": "no-store"},
            )
        if isinstance(error, aiohttp.ClientError):
            return Response("Site is unreachable", status_code=502)
        raise

    async def close():
        upstream.close()
        await client.close()

    async def body():
        try:
            async for chunk in upstream.content.iter_any():
                yield chunk
        finally:
            await close()

    response = StreamingResponse(body(), status_code=upstream.status, background=BackgroundTask(close))
    blocked = _HOP | {v.strip().lower() for v in upstream.headers.get("Connection", "").split(",")}
    for key, value in upstream.raw_headers:
        name_lower = key.decode("latin-1").lower()
        if name_lower in blocked:
            continue
        text = value.decode("latin-1")
        if name_lower == "location":
            url = urlsplit(text)
            backend = urlsplit(target)
            if url.scheme in {"http", "https"} and url.netloc == backend.netloc:
                text = url.path + (f"?{url.query}" if url.query else "") + (f"#{url.fragment}" if url.fragment else "")
            if text.startswith("/") and not text.startswith(("//", prefix)):
                text = prefix + text.lstrip("/")
        if name_lower == "set-cookie":
            text = re.sub(r"(?i)(;\s*path=)(/[^;]*)", lambda m: m[1] + (m[2] if m[2].startswith(prefix) else prefix + m[2].lstrip("/")), text)
        response.raw_headers.append((key.lower(), text.encode("latin-1")))
    return response


@site_relay.websocket("/s/{name}/{path:path}")
async def site_socket(socket: WebSocket, name: str, path: str):
    target = await site_target(name)
    if not target:
        await socket.close(code=1008)
        return
    prefix = f"/s/{quote(name, safe='')}/"
    protocols = [p.strip() for p in socket.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    headers = {k: v for k, v in headers_to_upstream(socket.headers, prefix).items()
               if not k.lower().startswith("sec-websocket-")}
    async with aiohttp.ClientSession() as client:
        try:
            async with client.ws_connect(target_url(target, path, socket.url.query), headers=headers, protocols=protocols) as upstream:
                await socket.accept(subprotocol=upstream.protocol)

                async def to_site():
                    while True:
                        message = await socket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if message.get("text") is not None:
                            await upstream.send_str(message["text"])
                        elif message.get("bytes") is not None:
                            await upstream.send_bytes(message["bytes"])

                async def to_browser():
                    async for message in upstream:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            await socket.send_text(message.data)
                        elif message.type == aiohttp.WSMsgType.BINARY:
                            await socket.send_bytes(message.data)

                tasks = [asyncio.create_task(to_site()), asyncio.create_task(to_browser())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (aiohttp.ClientError, WebSocketDisconnect):
            pass
        finally:
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.close()


async def ensure_site_gateway():
    """Reuse our verified gateway or start one detached service, never an agent."""
    import httpx
    from agentevolver.paths import P, path_manager

    address = f"http://127.0.0.1:{GATEWAY}"
    async with httpx.AsyncClient(timeout=.5, trust_env=False) as client:
        async def ready():
            try:
                response = await client.get(address + "/_sites/health")
            except httpx.RequestError:
                return False
            try:
                valid = response.json().get("service") == "agentevolver-sites"
            except (ValueError, AttributeError):
                valid = False
            if not valid:
                raise RuntimeError(f"Port {GATEWAY} is occupied by a different service")
            return True

        if not await ready():
            log = path_manager.resolve_under(path_manager.get(P.DEPLOY, create=True), "gateway.log")
            with log.open("ab") as output:
                process = subprocess.Popen(
                    [sys.executable, "-m", "agentevolver.gateway.sites"],
                    # Export the package root, not this interpreter's standard library:
                    # deployed apps may use a different Python executable.
                    env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (
                        str(path_manager.package_resource().parent),
                        os.environ.get("PYTHONPATH"),
                    )))},
                    stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
            for _ in range(50):
                if await ready():
                    break
                if process.poll() is not None:
                    raise RuntimeError(f"Site gateway failed to start; see {log}")
                await asyncio.sleep(.1)
            else:
                process.terminate()
                raise RuntimeError(f"Site gateway did not become ready; see {log}")
    os.environ.setdefault("GATEWAY_PUBLIC_BASE", address)
    return os.environ["GATEWAY_PUBLIC_BASE"].rstrip("/")


if __name__ == "__main__":
    import uvicorn
    app = FastAPI(title="AgentEvolver Sites", docs_url=None, redoc_url=None)
    app.include_router(site_relay)
    @app.get("/")
    async def index():
        return RedirectResponse("/sites/")
    uvicorn.run(app, host="127.0.0.1", port=GATEWAY, log_level="warning")
