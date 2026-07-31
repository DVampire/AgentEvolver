"""Host-side egress relay: the single way out of a network-less sandbox.

The sandbox is started with no network interface beyond loopback, and a Unix socket
belonging to this relay is bind-mounted into it. Inside, a small forwarder
(`agentevolver.sandbox.forwarder`) accepts proxy connections on loopback and splices
them to that socket, so the sandbox's `HTTPS_PROXY` points at something whose policy it
cannot change: this process runs on the host, outside the sandbox, and every connection
it opens has been checked against a :class:`~agentevolver.sandbox.netpolicy.NetworkPolicy`
first.

Two properties follow, and they are the reason for the indirection:

- **A missing rule fails closed.** The sandbox has no route to the internet at all, so
  an unlisted host is not reachable by any means — not by a different port, not by a raw
  socket, not by resolving an address out of band. It is not a filter that has to catch
  everything; it is the absence of a path.
- **Attempts are recorded.** `decisions` accumulates every host a sandbox tried to
  reach, so "the work had no internet access" becomes a claim a run can substantiate
  rather than a property of a config file someone hopes is right.

A Unix socket rather than a loopback port on the host: a port would be reachable by
anything else on the machine, and this relay is deliberately an open proxy to its
allowlist. File permissions on a socket are a boundary; a listening port is not.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from agentevolver.logger import logger
from agentevolver.sandbox.netpolicy import NetworkDecision, NetworkPolicy

#: Cap on the request head we will buffer before giving up. A proxy request head is a
#: few hundred bytes; anything past this is not one, and reading unboundedly from a
#: client that never sends CRLFCRLF would be a way to exhaust memory.
_MAX_HEAD_BYTES = 64 * 1024

#: AF_UNIX paths are bounded by `sizeof(sockaddr_un.sun_path)` — 108 bytes on Linux,
#: including the terminator. Held below that with room to spare, because the failure is
#: an opaque `OSError: AF_UNIX path too long` at bind time rather than anything that
#: points at the path.
_MAX_UNIX_PATH = 100

#: Short, predictable home for relay sockets. Deliberately NOT under the session tree:
#: a session directory carries an owner and an instance id (a ProgramBench instance id
#: alone runs to ~30 characters), and nesting a socket under it overruns the AF_UNIX
#: limit for perfectly ordinary names. The socket is transport, not an artifact worth
#: keeping next to a run's outputs.
_SOCKET_DIR = Path(os.environ.get("AGENTEVOLVER_EGRESS_SOCKET_DIR", "/tmp/agentevolver-egress"))

_CONNECT_OK = b"HTTP/1.1 200 Connection established\r\n\r\n"


def default_socket_path(key: str) -> Path:
    """A short, collision-resistant socket path for `key` (e.g. an instance id).

    The readable part of the name is truncated and a hash of the full key appended, so
    two long keys sharing a prefix cannot land on the same socket while the path stays
    comfortably inside the AF_UNIX limit.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", key or "sandbox").strip("-").lower()[:24]
    digest = hashlib.sha256((key or "sandbox").encode("utf-8")).hexdigest()[:8]
    return _SOCKET_DIR / f"{slug}-{digest}.sock"


def _http_error(status: str, detail: str) -> bytes:
    """A proxy error whose body the caller will actually read.

    The agent sees this text when a fetch is refused, so it says what happened and that
    retrying will not help — otherwise a blocked host reads as a flaky network and gets
    retried until the step budget is gone.
    """
    body = detail.encode("utf-8")
    return (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("ascii") + body


def _parse_target(request_line: str) -> Tuple[Optional[str], Optional[int], str]:
    """Extract (host, port, method) from a proxy request line.

    Handles the two forms a proxy sees: `CONNECT host:port HTTP/1.1` for tunnelled TLS,
    and an absolute-URI request line (`GET http://host/path HTTP/1.1`) for plain HTTP.
    """
    parts = request_line.split()
    if len(parts) < 2:
        return None, None, ""
    method, target = parts[0].upper(), parts[1]

    if method == "CONNECT":
        host, _, port = target.rpartition(":")
        return (host or target), (int(port) if port.isdigit() else 443), method

    if "://" in target:
        scheme, _, rest = target.partition("://")
        authority = rest.split("/", 1)[0]
        host, _, port = authority.rpartition(":")
        default = 443 if scheme == "https" else 80
        return (host or authority), (int(port) if port.isdigit() else default), method

    return None, None, method


class EgressRelay:
    """Serves a Unix socket, proxying only what `policy` permits."""

    def __init__(
        self,
        socket_path: str | os.PathLike,
        policy: NetworkPolicy,
        *,
        on_decision: Optional[Callable[[NetworkDecision], None]] = None,
        connect_timeout: float = 30.0,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.policy = policy
        self.decisions: List[NetworkDecision] = []
        self._on_decision = on_decision
        self._connect_timeout = connect_timeout
        self._server: Optional[asyncio.AbstractServer] = None

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> "EgressRelay":
        if len(str(self.socket_path)) > _MAX_UNIX_PATH:
            raise ValueError(
                f"egress socket path is {len(str(self.socket_path))} characters, over the "
                f"{_MAX_UNIX_PATH}-character limit for a Unix socket: {self.socket_path}. "
                f"Use relay.default_socket_path(<key>) instead of a path under the "
                f"session tree — bind() would otherwise fail with a bare "
                f"'AF_UNIX path too long'."
            )
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        # A leftover socket file from a killed run makes bind fail with EADDRINUSE even
        # though nothing is listening.
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle, path=str(self.socket_path))
        # The sandbox runs as a different user than this process may; the socket is the
        # only thing it needs, and it sits in a directory we created.
        os.chmod(self.socket_path, 0o666)
        logger.info(f"| 🛡️  Egress relay on {self.socket_path} — {self.policy.describe()}")
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()

    async def __aenter__(self) -> "EgressRelay":
        return await self.start()

    async def __aexit__(self, *_exc) -> None:
        await self.stop()

    # -------------------------------------------------------------------- audit
    @property
    def denied(self) -> List[NetworkDecision]:
        return [d for d in self.decisions if not d.allowed]

    def audit(self) -> dict:
        """A summary suitable for dropping into a run's results file."""
        return {
            "policy": self.policy.describe(),
            "attempts": len(self.decisions),
            "denied": [
                {"host": d.host, "port": d.port, "reason": d.reason, "at": d.at.isoformat()}
                for d in self.denied
            ],
            "allowed_hosts": sorted({d.host for d in self.decisions if d.allowed}),
        }

    def write_audit(self, path: str | os.PathLike) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.audit(), indent=2), encoding="utf-8")

    def _record(self, decision: NetworkDecision) -> None:
        self.decisions.append(decision)
        if not decision.allowed:
            # Worth a warning, not a debug line: a denial in a run that was supposed to
            # be offline is the signal that the work tried to reach outside.
            logger.warning(f"| 🚫 Egress denied: {decision.host}:{decision.port} ({decision.reason})")
        if self._on_decision is not None:
            with contextlib.suppress(Exception):
                self._on_decision(decision)

    # ----------------------------------------------------------------- plumbing
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await self._read_head(reader)
            if head is None:
                writer.write(_http_error("400 Bad Request", "malformed proxy request"))
                await writer.drain()
                return

            request_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            host, port, method = _parse_target(request_line)
            if not host or port is None:
                writer.write(_http_error(
                    "400 Bad Request",
                    "this is an HTTP proxy; send CONNECT or an absolute-URI request",
                ))
                await writer.drain()
                return

            decision = self.policy.decide(host, port)
            self._record(decision)
            if not decision.allowed:
                writer.write(_http_error(
                    "403 Forbidden",
                    f"{host}:{port} is blocked by this sandbox's egress policy "
                    f"({decision.reason}). This is deliberate and permanent — retrying, "
                    f"using a different port, or resolving the address another way will "
                    f"not work.",
                ))
                await writer.drain()
                return

            await self._splice_to_target(head, method, host, port, reader, writer)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️ Egress relay error: {e}")
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    @staticmethod
    async def _read_head(reader: asyncio.StreamReader) -> Optional[bytes]:
        """Read up to and including the blank line ending the request head."""
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.LimitOverrunError:
            return None
        except asyncio.IncompleteReadError as e:
            head = e.partial
        if not head or len(head) > _MAX_HEAD_BYTES:
            return None
        return head

    async def _splice_to_target(
        self,
        head: bytes,
        method: str,
        host: str,
        port: int,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        try:
            target_reader, target_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=self._connect_timeout
            )
        except (asyncio.TimeoutError, OSError) as e:
            # The host was permitted but unreachable — a real network error, and it must
            # not be reported as a policy denial or the audit trail stops meaning
            # anything.
            client_writer.write(_http_error("502 Bad Gateway", f"cannot reach {host}:{port}: {e}"))
            await client_writer.drain()
            return

        try:
            if method == "CONNECT":
                client_writer.write(_CONNECT_OK)
                await client_writer.drain()
            else:
                # Plain HTTP: replay the head verbatim. An absolute-URI request line is
                # something origin servers are required to accept, so no rewriting is
                # needed and none is done — rewriting would be a chance to corrupt a
                # request we are only meant to be carrying.
                target_writer.write(head)
                await target_writer.drain()

            await asyncio.gather(
                _pump(client_reader, target_writer),
                _pump(target_reader, client_writer),
                return_exceptions=True,
            )
        finally:
            with contextlib.suppress(Exception):
                target_writer.close()
                await target_writer.wait_closed()


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy one direction until EOF, then half-close so the peer sees the EOF too."""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        with contextlib.suppress(Exception):
            if writer.can_write_eof():
                writer.write_eof()
