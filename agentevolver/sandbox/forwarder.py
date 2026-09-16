"""In-sandbox forwarder: turns a mounted Unix socket into a loopback proxy port.

Runs *inside* a sandbox that has no network interface but loopback. It listens on
`127.0.0.1:<port>` and splices every connection to the host-side
:class:`~agentevolver.sandbox.relay.EgressRelay` over a bind-mounted Unix socket, so
`HTTPS_PROXY=http://127.0.0.1:<port>` gives the processes in the sandbox exactly one
route out — one whose allow/deny decisions are made on the host.

It carries bytes and nothing else. Deliberately: policy here would be policy inside the
blast radius, editable by whatever the sandbox is running. Point this at a relay, not at
a rule.

Usage inside the sandbox:

    python -m agentevolver.sandbox.forwarder --port 8888 --socket /run/egress.sock

It is intentionally free of framework imports — it has to be able to start before, and
independently of, anything else in the sandbox.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
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


def _refusal(detail: str) -> bytes:
    body = detail.encode("utf-8")
    return (
        f"HTTP/1.1 502 Bad Gateway\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("ascii") + body


class Forwarder:
    def __init__(self, socket_path: str, host: str = "127.0.0.1", port: int = 8888) -> None:
        self.socket_path = socket_path
        self.host = host
        self.port = port

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                relay_reader, relay_writer = await asyncio.open_unix_connection(self.socket_path)
            except OSError as e:
                # The socket missing means the sandbox was started without a relay. Say
                # so plainly: the alternative is a connection error that reads like a
                # transient network fault and invites retries that can never succeed.
                writer.write(_refusal(
                    f"no egress relay at {self.socket_path} ({e}). This sandbox has no "
                    f"network route; nothing outside it is reachable."
                ))
                await writer.drain()
                return
            try:
                await asyncio.gather(
                    _pump(reader, relay_writer),
                    _pump(relay_reader, writer),
                    return_exceptions=True,
                )
            finally:
                with contextlib.suppress(Exception):
                    relay_writer.close()
                    await relay_writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def serve_forever(self) -> None:
        server = await asyncio.start_server(self._handle, self.host, self.port)
        addrs = ", ".join(str(s.getsockname()) for s in server.sockets or [])
        print(f"forwarder listening on {addrs} -> {self.socket_path}", flush=True)
        async with server:
            await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--socket", default=os.environ.get("AGENTEVOLVER_EGRESS_SOCKET", "/run/egress.sock"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENTEVOLVER_EGRESS_PORT", "8888")))
    args = parser.parse_args(argv)
    try:
        asyncio.run(Forwarder(args.socket, args.host, args.port).serve_forever())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
