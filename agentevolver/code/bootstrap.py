"""The child half of the code runtime: run one program, talk to the host over stdio.

Standard library only, and it imports nothing from ``agentevolver`` on purpose. This
module is executed as a script in a fresh interpreter, so anything it imported would be
imported into the same process as the model's program — including the permission manager
the program must not be able to reach. The whole point of spending a process on this is
that the program's world contains no framework objects to monkeypatch.

The protocol is JSON, one object per line:

- host → child, first line: ``{"program", "bindings"}``
- child → host: ``{"t": "log", "text"}`` as each line is printed
- child → host: ``{"t": "call", "id", "name", "args"}`` for each tool call
- host → child: ``{"t": "reply", "id", "ok", "value"|"message"}``
- child → host: ``{"t": "done", "value", "failure"}`` once, last

Logs travel as they are printed rather than in the final message, so a program killed at
its time budget still returns what it had printed by then — which is usually the part
that says how far it got.

The program's own ``stdout`` is redirected into those log messages, so an ordinary
``print`` cannot corrupt the channel. A program that writes to file descriptor 1 directly
can; the host ignores what it cannot parse.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import re
import sys
import textwrap
import threading
import traceback
from typing import Any, Dict, Optional

#: The real stdout, captured before the program's is redirected. Every host-bound
#: message goes here.
_CHANNEL = sys.stdout
_CHANNEL_LOCK = threading.Lock()

#: Longest single line sent to the host. The host reads with a bounded stream reader, and
#: a program printing one 50MB line would otherwise break the channel rather than its own
#: output.
MAX_LINE_CHARS = 200_000

#: Name the program's frames carry in tracebacks. It is also what the line-number
#: correction below looks for.
PROGRAM_FILENAME = "<program>"

#: The program is compiled as the body of an async function, which costs one line at the
#: top. Reported line numbers are shifted back by this much so they match what the model
#: wrote.
_WRAPPER_LINES = 1


class ToolCallError(Exception):
    """A tool called from inside a program refused or failed.

    Program-visible by design: the model catches this to keep going after one call of a
    batch fails, exactly as it would branch on an error result over the wire.
    """

    def __init__(self, message: str, tool_name: str = ""):
        super().__init__(message)
        self.tool_name = tool_name


def _send(payload: Dict[str, Any]) -> None:
    """Write one message to the host. Blocking, and serialized across threads."""
    line = json.dumps(payload, ensure_ascii=False, default=str)
    if len(line) > MAX_LINE_CHARS:
        line = json.dumps(
            {"t": "log", "text": f"[a {len(line):,}-character line was dropped: too large to send]"}
        )
    with _CHANNEL_LOCK:
        _CHANNEL.write(line + "\n")
        _CHANNEL.flush()


class _LineLog(io.TextIOBase):
    """Stand-in for the program's stdout that forwards whole lines to the host."""

    def __init__(self) -> None:
        super().__init__()
        self._buffer = ""
        self._lock = threading.Lock()

    def write(self, text: str) -> int:  # type: ignore[override]
        with self._lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                _send({"t": "log", "text": line})
        return len(text)

    def writable(self) -> bool:  # type: ignore[override]
        return True

    def drain(self) -> None:
        """Send whatever was printed without a trailing newline."""
        with self._lock:
            if self._buffer:
                _send({"t": "log", "text": self._buffer})
                self._buffer = ""


class _Tools:
    """The ``tools`` global: one async function per name the host agreed to bind.

    Attribute and item access both work, so a tool whose name is not a Python identifier
    is still reachable as ``tools["odd-name"]`` without inventing an alias for it.
    """

    def __init__(self, names, call) -> None:
        object.__setattr__(self, "_names", tuple(names))
        object.__setattr__(self, "_call", call)

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return self._bind(name)

    def __getitem__(self, name: str):
        return self._bind(name)

    def __dir__(self):
        return list(self._names)

    def _bind(self, name: str):
        if name not in self._names:
            available = ", ".join(self._names) or "none"
            raise ToolCallError(f"No tool named {name!r} is callable here. Callable: {available}.", name)

        async def invoke(**kwargs):
            return await self._call(name, kwargs)

        invoke.__name__ = name
        return invoke


def _correct_line_numbers(text: str) -> str:
    """Point tracebacks at the line the model wrote, not the line after the wrapper."""

    def shift(match: "re.Match[str]") -> str:
        return f'File "{PROGRAM_FILENAME}", line {max(1, int(match.group(1)) - _WRAPPER_LINES)}'

    return re.sub(rf'File "{re.escape(PROGRAM_FILENAME)}", line (\d+)', shift, text)


def _format_exception(exc: BaseException) -> str:
    """The program's traceback, without this module's frames in it.

    A model reading its own program's failure should see its own lines. The bootstrap
    frames are noise it cannot act on, and they invite it to "fix" code it never wrote.
    """
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    kept = [line for line in lines if __file__ not in line]
    return _correct_line_numbers("".join(kept).strip())


async def _run(init: Dict[str, Any]) -> None:
    loop = asyncio.get_running_loop()
    pending: Dict[int, asyncio.Future] = {}
    counter = iter(range(1, 1_000_000_000))

    def reader() -> None:
        """Feed host replies back to whoever is awaiting them.

        A thread rather than an event-loop reader: the program may hold the loop busy
        between awaits, and a reply that arrives then must still be recorded.
        """
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            future = pending.pop(int(message.get("id", -1)), None)
            if future is None or future.cancelled():
                continue
            ok = bool(message.get("ok"))
            payload = message.get("value") if ok else str(message.get("message") or "")
            loop.call_soon_threadsafe(future.set_result, (ok, payload))

    threading.Thread(target=reader, daemon=True, name="code-runtime-replies").start()

    async def call(name: str, args: Dict[str, Any]) -> Any:
        identifier = next(counter)
        future: asyncio.Future = loop.create_future()
        pending[identifier] = future
        _send({"t": "call", "id": identifier, "name": name, "args": args})
        ok, payload = await future
        if not ok:
            raise ToolCallError(str(payload), name)
        return payload

    # The working directory is importable, but last: a program in a Python project should
    # be able to `import` that project, and no file in it should be able to take over
    # `types` or `json` for the interpreter running it.
    sys.path.append(os.getcwd())

    body = init.get("program") or ""
    source = "async def __program__():\n" + textwrap.indent(body if body.strip() else "pass", "    ")

    value: Any = None
    failure: Optional[Dict[str, str]] = None
    namespace: Dict[str, Any] = {
        "tools": _Tools(init.get("bindings") or (), call),
        "ToolCallError": ToolCallError,
        "__name__": "__program__",
    }
    output = _LineLog()
    try:
        compiled = compile(source, PROGRAM_FILENAME, "exec")
    except SyntaxError as error:
        line = max(1, (error.lineno or 1) - _WRAPPER_LINES)
        failure = {"kind": "exception", "message": f"SyntaxError on line {line}: {error.msg}"}
    else:
        exec(compiled, namespace)  # noqa: S102 — running the model's program is the point
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                value = await namespace["__program__"]()
        except BaseException as error:  # noqa: BLE001 — every failure is the program's news
            failure = {"kind": "exception", "message": _format_exception(error)}
        finally:
            output.drain()

    _send({"t": "done", "value": value, "failure": failure})


def main() -> None:
    first = sys.stdin.readline()
    if not first.strip():
        return
    asyncio.run(_run(json.loads(first)))


if __name__ == "__main__":
    main()
