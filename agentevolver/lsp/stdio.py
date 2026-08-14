"""A provider that speaks LSP to a language server over its stdin and stdout.

Three layers, smallest first, because each one fails differently and the failures must
not be confused. Framing turns bytes into messages and refuses a message large enough to
exhaust memory. The connection owns one child process, correlates request ids, and
answers the handful of requests a server makes back at its client. The server object owns
one `(session, workspace)` process: the initialize handshake, the capability check, and
the open-ask-close cycle around a single query.

The document lifecycle is deliberately transient. Each query opens the file, asks, and
closes it in a `finally`, so the server never holds a document this process might have
edited since. Keeping documents open would be faster and would require this module to
track every write made by every other tool, which it cannot see.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agentevolver.logger import logger
from agentevolver.lsp.types import (
    Hover,
    Location,
    LspError,
    LspErrorCode,
    LspOperation,
    LspProvider,
    LspQuery,
    LspResult,
    Position,
    Range,
    ResultKind,
    Symbol,
    symbol_kind_name,
)

#: Header block bound. A server that never sends the blank line separating headers from
#: body would otherwise grow this buffer until the host runs out of memory.
MAX_HEADER_BYTES = 1 << 16

#: Largest single message accepted from a server. Sixteen megabytes is far past any real
#: hover or symbol list; past it, the server is broken or hostile.
MAX_MESSAGE_BYTES = 16_000_000

#: Largest source file this host will open. A generated file above this is not worth a
#: language server's memory, and the refusal names the size.
MAX_DOCUMENT_BYTES = 4_000_000

#: Stderr kept for diagnostics. A server that fails to start usually explains itself
#: there, and that explanation is the only useful thing in the failure.
MAX_STDERR_CHARS = 4_000

#: Handshake budget. A cold server indexes a large workspace before it answers, which is
#: slow once per workspace rather than once per query.
INITIALIZE_TIMEOUT = 30.0

#: One query's budget. Well under the tool's own call budget, so the tool reports which
#: request stalled instead of being cut off naming neither.
REQUEST_TIMEOUT = 20.0

#: How long a server is given to exit on its own before it is signalled.
SHUTDOWN_TIMEOUT = 5.0

#: Live servers one session may hold. Each is a real process indexing a workspace.
MAX_SERVERS_PER_SESSION = 4

#: The extensions the bundled Python provider claims.
DEFAULT_PYTHON_EXTENSIONS = (".py", ".pyi")

_HEADER_SEPARATOR = b"\r\n\r\n"

#: What the client tells the server it can do. No dynamic registration, and UTF-16
#: positions, which is what the seam's coordinates already are.
CLIENT_CAPABILITIES: Dict[str, Any] = {
    "general": {"positionEncodings": ["utf-16"]},
    "workspace": {"workspaceFolders": True, "configuration": True},
    "textDocument": {
        "synchronization": {"dynamicRegistration": False},
        "hover": {"contentFormat": ["markdown", "plaintext"]},
        "definition": {"linkSupport": True},
        "references": {},
        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
    },
}

#: Which `ServerCapabilities` field backs each operation, and which request asks it.
_OPERATION_WIRE: Dict[LspOperation, Tuple[str, str]] = {
    LspOperation.DEFINITION: ("definitionProvider", "textDocument/definition"),
    LspOperation.REFERENCES: ("referencesProvider", "textDocument/references"),
    LspOperation.HOVER: ("hoverProvider", "textDocument/hover"),
    LspOperation.SYMBOLS: ("documentSymbolProvider", "textDocument/documentSymbol"),
}

#: Server-to-client requests acknowledged with an empty result. This host registers
#: nothing dynamically, but a server that asks and is never answered blocks its own
#: startup waiting for a reply that will not come.
_LIFECYCLE_ACKS = frozenset({
    "client/registerCapability",
    "client/unregisterCapability",
    "window/workDoneProgress/create",
})


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #
def encode_message(message: Dict[str, Any]) -> bytes:
    """Frame one JSON-RPC message as `Content-Length: N\\r\\n\\r\\n<utf-8 json>`."""
    body = json.dumps(message).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n%s" % (len(body), body)


class MessageDecoder:
    """Turns a byte stream into whole JSON-RPC messages.

    A stream decoder rather than a per-read parse, because a pipe splits wherever it
    likes: one read can carry half a header, and the next can carry the rest of that
    message plus two more. Treating each read as a message is the bug that makes a
    language server client work in tests and fail against a real server under load.
    """

    def __init__(self, max_message_bytes: int = MAX_MESSAGE_BYTES) -> None:
        self._buffer = bytearray()
        self._max_message_bytes = max_message_bytes

    def push(self, chunk: bytes) -> List[Dict[str, Any]]:
        """Add bytes and return every message that is now complete, in arrival order."""
        self._buffer.extend(chunk)
        messages: List[Dict[str, Any]] = []
        while True:
            message = self._next()
            if message is None:
                return messages
            messages.append(message)

    def _next(self) -> Optional[Dict[str, Any]]:
        separator = self._buffer.find(_HEADER_SEPARATOR)
        if separator < 0:
            if len(self._buffer) > MAX_HEADER_BYTES:
                raise LspError(f"language server sent {len(self._buffer)} bytes of header "
                               f"with no end to it", LspErrorCode.MALFORMED_RESPONSE)
            return None
        header = bytes(self._buffer[:separator]).decode("ascii", "replace")
        length = _content_length(header)
        if length > self._max_message_bytes:
            raise LspError(f"language server announced a {length}-byte message, over the "
                           f"{self._max_message_bytes}-byte limit",
                           LspErrorCode.MALFORMED_RESPONSE)
        start = separator + len(_HEADER_SEPARATOR)
        end = start + length
        if len(self._buffer) < end:
            return None
        body = bytes(self._buffer[start:end])
        del self._buffer[:end]
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LspError(f"language server sent a body that is not JSON: {error}",
                           LspErrorCode.MALFORMED_RESPONSE) from error
        if not isinstance(message, dict):
            raise LspError("language server sent a JSON value that is not a message object",
                           LspErrorCode.MALFORMED_RESPONSE)
        return message


def _content_length(header: str) -> int:
    """Read `Content-Length` out of a header block, case-insensitively."""
    for line in header.split("\r\n"):
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError:
                raise LspError(f"language server sent a bad Content-Length: {line!r}",
                               LspErrorCode.MALFORMED_RESPONSE) from None
            if length < 0:
                raise LspError(f"language server sent a negative Content-Length: {line!r}",
                               LspErrorCode.MALFORMED_RESPONSE)
            return length
    raise LspError(f"language server sent a header with no Content-Length: {header!r}",
                   LspErrorCode.MALFORMED_RESPONSE)


# --------------------------------------------------------------------------- #
# One child process
# --------------------------------------------------------------------------- #
class _Pending:
    """A request waiting for its response."""

    __slots__ = ("event", "result", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: Optional[LspError] = None


class LspConnection:
    """One language-server process and the JSON-RPC traffic over its pipes."""

    def __init__(self, command: Sequence[str], cwd: str,
                 env: Optional[Dict[str, str]] = None) -> None:
        self._decoder = MessageDecoder()
        self._pending: Dict[int, _Pending] = {}
        self._next_id = 1
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._failure: Optional[LspError] = None
        self._stderr: List[str] = []

        child_env = {**os.environ, **(env or {})}
        try:
            self._process = subprocess.Popen(
                list(command), cwd=cwd, env=child_env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                bufsize=0,
                # Its own process group, so terminating the server does not deliver a
                # signal to the agent process that started it.
                start_new_session=True,
            )
        except OSError as error:
            raise LspError(f"could not start language server {command[0]!r}: {error}",
                           LspErrorCode.SERVER_FAILED) from error

        self._reader = threading.Thread(target=self._read_loop,
                                        name=f"lsp-{self._process.pid}", daemon=True)
        self._reader.start()
        self._draining = threading.Thread(target=self._drain_stderr,
                                          name=f"lsp-err-{self._process.pid}", daemon=True)
        self._draining.start()

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def alive(self) -> bool:
        return self._failure is None and self._process.poll() is None

    @property
    def stderr_tail(self) -> str:
        return "".join(self._stderr)[-MAX_STDERR_CHARS:]

    # -- talking -------------------------------------------------------

    def request(self, method: str, params: Any, timeout: float) -> Any:
        """Send a request and wait for its answer.

        On timeout the server is asked to cancel and the wait ends anyway. A caller that
        kept waiting would hold the whole queue behind a server that has already decided
        not to answer.
        """
        with self._lock:
            if self._failure is not None:
                raise self._failure
            request_id = self._next_id
            self._next_id += 1
            pending = _Pending()
            self._pending[request_id] = pending

        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        if not pending.event.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            self.notify("$/cancelRequest", {"id": request_id})
            raise LspError(f"language server did not answer {method} within {timeout:g}s",
                           LspErrorCode.TIMEOUT)
        if pending.error is not None:
            raise pending.error
        return pending.result

    def notify(self, method: str, params: Any) -> None:
        """Send a notification. Best-effort: a dead server has already failed the query."""
        try:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})
        except LspError:
            pass

    def _write(self, message: Dict[str, Any]) -> None:
        with self._write_lock:
            failure = self._failure
            if failure is not None:
                raise failure
            try:
                self._process.stdin.write(encode_message(message))
                self._process.stdin.flush()
            except (OSError, ValueError) as error:
                failure = LspError(f"language server stopped reading its input: {error}",
                                   LspErrorCode.SERVER_FAILED)
                self._fail(failure)
                raise failure from error

    # -- listening -----------------------------------------------------

    def _read_loop(self) -> None:
        while True:
            try:
                chunk = self._process.stdout.read(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            try:
                messages = self._decoder.push(chunk)
            except LspError as error:
                # The stream position is unrecoverable once framing is wrong: everything
                # after it is read at the wrong offset. Fail the connection rather than
                # answer later requests with fragments of earlier ones.
                self._fail(error)
                self.close()
                return
            for message in messages:
                self._dispatch(message)
        self._fail(LspError(self._exit_reason(), LspErrorCode.SERVER_FAILED))

    def _drain_stderr(self) -> None:
        """Keep the tail of stderr, and keep reading it.

        Not only for diagnostics: a pipe nobody empties fills, and a server whose stderr
        is full blocks on its next log line, which looks exactly like a server thinking
        hard about a query.
        """
        stream = self._process.stderr
        while True:
            try:
                chunk = stream.read(4096)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            self._stderr.append(chunk.decode("utf-8", "replace"))
            if len(self._stderr) > 64:
                self._stderr = ["".join(self._stderr)[-MAX_STDERR_CHARS:]]

    def _dispatch(self, message: Dict[str, Any]) -> None:
        method = message.get("method")
        message_id = message.get("id")
        if isinstance(method, str) and message_id is not None:
            self._answer_server_request(message_id, method)
            return
        if isinstance(method, str):
            return                      # a notification: diagnostics, logs, progress
        if not isinstance(message_id, int):
            return
        with self._lock:
            pending = self._pending.pop(message_id, None)
        if pending is None:
            return                      # a cancelled request answered anyway
        error = message.get("error")
        if isinstance(error, dict):
            pending.error = LspError(
                f"language server refused the request: {error.get('message', error)}",
                LspErrorCode.SERVER_FAILED)
        else:
            pending.result = message.get("result")
        pending.event.set()

    def _answer_server_request(self, message_id: Any, method: str) -> None:
        """Reply to the few requests a server makes of its client.

        `workspace/applyEdit` is refused rather than ignored. This tool declares that it
        does not mutate, and a server that could write files through it would make that
        declaration false — which is the declaration plan mode admits the tool on.
        """
        if method == "workspace/configuration":
            reply: Dict[str, Any] = {"id": message_id, "result": []}
        elif method in _LIFECYCLE_ACKS:
            reply = {"id": message_id, "result": None}
        else:
            reply = {"id": message_id,
                     "error": {"code": -32601, "message": f"{method} is not permitted by this client"}}
        try:
            self._write({"jsonrpc": "2.0", **reply})
        except LspError:
            pass

    # -- ending --------------------------------------------------------

    def _exit_reason(self) -> str:
        code = self._process.poll()
        tail = self.stderr_tail.strip()
        reason = f"language server exited (code {code})"
        return f"{reason}; stderr: {tail}" if tail else reason

    def _fail(self, failure: LspError) -> None:
        """Record the first failure and release everything waiting on this connection."""
        with self._lock:
            if self._failure is None:
                self._failure = failure
            waiting = list(self._pending.values())
            self._pending.clear()
        for pending in waiting:
            pending.error = failure
            pending.event.set()

    def close(self) -> None:
        """End the process. Idempotent, and it does not return until the child is gone."""
        process = self._process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=SHUTDOWN_TIMEOUT)
                except subprocess.TimeoutExpired:
                    logger.warning(f"| ⚠️ Language server {process.pid} survived SIGKILL")
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        self._fail(LspError("language server was closed", LspErrorCode.SERVER_FAILED))


# --------------------------------------------------------------------------- #
# One initialized server, for one workspace
# --------------------------------------------------------------------------- #
@dataclass
class ServerSpec:
    """How to launch one language server and how long to wait for it."""

    command: Sequence[str]
    env: Dict[str, str] = field(default_factory=dict)
    initialization_options: Any = None
    request_timeout: float = REQUEST_TIMEOUT
    initialize_timeout: float = INITIALIZE_TIMEOUT


class LanguageServer:
    """One process, initialized against one workspace, answering one query at a time.

    Serialized, because a query is three messages — open, ask, close — around a document
    the server keys by URI. Two overlapping queries on the same file would interleave
    into an open, an open, a close, and an ask against a document that is no longer there.
    """

    def __init__(self, spec: ServerSpec, workspace_root: str) -> None:
        self.workspace_root = workspace_root
        self.workspace_uri = Path(workspace_root).as_uri()
        self.last_used = time.monotonic()
        self.busy = False
        self._spec = spec
        self._lock = threading.Lock()
        self._capabilities: Optional[Dict[str, Any]] = None
        self._connection = LspConnection(spec.command, cwd=workspace_root, env=spec.env)
        logger.info(f"| 🔎 Language server {spec.command[0]} started for {workspace_root} "
                    f"(pid {self._connection.pid})")

    @property
    def pid(self) -> int:
        return self._connection.pid

    @property
    def alive(self) -> bool:
        return self._connection.alive

    def query(self, request: LspQuery, source: "SourceFile") -> LspResult:
        """Run one query: handshake if needed, open the file, ask, close the file."""
        with self._lock:
            self.busy = True
            try:
                self._ensure_initialized()
                return self._run(request, source)
            finally:
                self.busy = False
                self.last_used = time.monotonic()

    def _ensure_initialized(self) -> None:
        if self._capabilities is not None:
            return
        result = self._connection.request("initialize", {
            # Null, not our pid: a server told to watch this process would kill itself
            # when the agent restarts, and in a container it would watch pid 1.
            "processId": None,
            "rootUri": self.workspace_uri,
            "workspaceFolders": [{"uri": self.workspace_uri, "name": "workspace"}],
            "capabilities": CLIENT_CAPABILITIES,
            "initializationOptions": self._spec.initialization_options,
        }, timeout=self._spec.initialize_timeout)
        capabilities = (result or {}).get("capabilities")
        if not isinstance(capabilities, dict):
            raise LspError("language server answered initialize without capabilities",
                           LspErrorCode.MALFORMED_RESPONSE)
        encoding = capabilities.get("positionEncoding")
        if encoding not in (None, "utf-16"):
            # Every column in every answer would be off on any line with a non-ASCII
            # character, and silently: the position looks plausible and points elsewhere.
            raise LspError(f"language server negotiated {encoding!r} positions; this client "
                           f"only speaks utf-16", LspErrorCode.UNSUPPORTED_OPERATION)
        if not _supports_open_close(capabilities.get("textDocumentSync")):
            raise LspError("language server does not accept the transient document open "
                           "this client needs", LspErrorCode.UNSUPPORTED_OPERATION)
        self._capabilities = capabilities
        self._connection.notify("initialized", {})

    def _run(self, request: LspQuery, source: "SourceFile") -> LspResult:
        capability, method = _OPERATION_WIRE[request.operation]
        if not _supports(self._capabilities or {}, capability):
            raise LspError(f"language server does not answer {request.operation.value} "
                           f"for {request.language_id}", LspErrorCode.UNSUPPORTED_OPERATION)

        self._connection.notify("textDocument/didOpen", {"textDocument": {
            "uri": source.uri, "languageId": request.language_id,
            "version": 1, "text": source.text}})
        try:
            params: Dict[str, Any] = {"textDocument": {"uri": source.uri}}
            if request.position is not None:
                params["position"] = request.position.as_wire()
            if request.operation is LspOperation.REFERENCES:
                # Always with the declaration. A caller weighing a rename wants every
                # site, and a flag would let it ask for an answer that omits the one
                # site it is about to change.
                params["context"] = {"includeDeclaration": True}
            payload = self._connection.request(method, params,
                                               timeout=self._spec.request_timeout)
        finally:
            self._connection.notify("textDocument/didClose",
                                    {"textDocument": {"uri": source.uri}})
        return self._normalize(request.operation, payload)

    def _normalize(self, operation: LspOperation, payload: Any) -> LspResult:
        if operation is LspOperation.HOVER:
            return LspResult(kind=ResultKind.HOVER, hover=normalize_hover(payload),
                             workspace_uri=self.workspace_uri)
        if operation is LspOperation.SYMBOLS:
            return LspResult(kind=ResultKind.SYMBOLS, symbols=normalize_symbols(payload),
                             workspace_uri=self.workspace_uri)
        return LspResult(kind=ResultKind.LOCATIONS, locations=normalize_locations(payload),
                         workspace_uri=self.workspace_uri)

    def close(self) -> None:
        """Ask the server to shut down, then make sure it did."""
        try:
            if self._connection.alive and self._capabilities is not None:
                self._connection.request("shutdown", None, timeout=SHUTDOWN_TIMEOUT)
                self._connection.notify("exit", None)
        except LspError:
            pass                        # a server that will not shut down gets signalled
        self._connection.close()


def _supports(capabilities: Dict[str, Any], name: str) -> bool:
    """Whether a `ServerCapabilities` entry is present. Options objects mean yes."""
    value = capabilities.get(name)
    if value is None or value is False:
        return False
    return True


def _supports_open_close(sync: Any) -> bool:
    """Whether the server accepts `didOpen`/`didClose`.

    The legacy integer form implies open and close for `Full` and `Incremental`. The
    options form must say `openClose` explicitly, because the protocol reads an omitted
    one as false — reading it as true is how a client ends up sending documents to a
    server that silently discards them.
    """
    if isinstance(sync, bool):
        return False
    if isinstance(sync, int):
        return sync in (1, 2)
    if isinstance(sync, dict):
        return sync.get("openClose") is True
    return False


# --------------------------------------------------------------------------- #
# Wire shapes to seam shapes
# --------------------------------------------------------------------------- #
def _range(value: Any) -> Range:
    if not isinstance(value, dict):
        raise LspError("language server sent a location with no range",
                       LspErrorCode.MALFORMED_RESPONSE)
    return Range(start=_position(value.get("start")), end=_position(value.get("end")))


def _position(value: Any) -> Position:
    if not isinstance(value, dict):
        raise LspError("language server sent a range with no position",
                       LspErrorCode.MALFORMED_RESPONSE)
    line, character = value.get("line"), value.get("character")
    if not isinstance(line, int) or not isinstance(character, int) or line < 0 or character < 0:
        raise LspError(f"language server sent an impossible position {value!r}",
                       LspErrorCode.MALFORMED_RESPONSE)
    return Position(line=line, character=character)


def normalize_locations(payload: Any) -> List[Location]:
    """Flatten `Location`, `Location[]`, `LocationLink[]`, or `null` into locations.

    Three shapes because the protocol allows all three for the same request, and which
    one arrives depends on the server and on whether the client advertised link support.
    A client that handles only the shape its favourite server sends reports "no
    definition" against every other server.
    """
    if payload is None:
        return []
    entries = payload if isinstance(payload, list) else [payload]
    locations: List[Location] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise LspError("language server sent a location that is not an object",
                           LspErrorCode.MALFORMED_RESPONSE)
        if isinstance(entry.get("targetUri"), str):
            locations.append(Location(uri=entry["targetUri"],
                                      range=_range(entry.get("targetSelectionRange"))))
        elif isinstance(entry.get("uri"), str):
            locations.append(Location(uri=entry["uri"], range=_range(entry.get("range"))))
        else:
            raise LspError(f"language server sent {entry!r}, which is neither a Location "
                           f"nor a LocationLink", LspErrorCode.MALFORMED_RESPONSE)
    return locations


def normalize_hover(payload: Any) -> Optional[Hover]:
    """Render the three `Hover.contents` encodings into one string, or `None` for nothing."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise LspError("language server sent a hover that is not an object",
                       LspErrorCode.MALFORMED_RESPONSE)
    contents = _hover_text(payload.get("contents"))
    if not contents:
        return None
    span = payload.get("range")
    return Hover(contents=contents, range=_range(span) if span is not None else None)


def _hover_text(contents: Any) -> str:
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        return "\n\n".join(part for part in (_marked_string(item) for item in contents) if part)
    if isinstance(contents, dict):
        if contents.get("kind") in ("markdown", "plaintext"):
            value = contents.get("value")
            if not isinstance(value, str):
                raise LspError("language server sent MarkupContent with no string value",
                               LspErrorCode.MALFORMED_RESPONSE)
            return value
        return _marked_string(contents)
    raise LspError(f"language server sent hover contents of type {type(contents).__name__}",
                   LspErrorCode.MALFORMED_RESPONSE)


def _marked_string(value: Any) -> str:
    """A `MarkedString`: plain text, or a language-tagged value rendered as a code fence."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("language"), str) \
            and isinstance(value.get("value"), str):
        return f"```{value['language']}\n{value['value']}\n```"
    raise LspError(f"language server sent {value!r} where a MarkedString belongs",
                   LspErrorCode.MALFORMED_RESPONSE)


def normalize_symbols(payload: Any) -> List[Symbol]:
    """Flatten `DocumentSymbol[]` (a tree) or `SymbolInformation[]` (already flat).

    Flattened rather than nested because the result is read as a list of places to go.
    The nesting is not lost: a child's `container` is its parent's name, which is the
    part a reader needs to tell three methods called `close` apart.
    """
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise LspError("language server sent a document symbol result that is not a list",
                       LspErrorCode.MALFORMED_RESPONSE)
    symbols: List[Symbol] = []
    _collect_symbols(payload, "", symbols)
    return symbols


def _collect_symbols(entries: Any, container: str, into: List[Symbol]) -> None:
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            raise LspError("language server sent a symbol that is not an object",
                           LspErrorCode.MALFORMED_RESPONSE)
        name = entry.get("name")
        if not isinstance(name, str):
            raise LspError(f"language server sent a symbol with no name: {entry!r}",
                           LspErrorCode.MALFORMED_RESPONSE)
        location = entry.get("location")
        if isinstance(location, dict):          # SymbolInformation: flat, carries a location
            span = _range(location.get("range"))
            parent = entry.get("containerName")
            into.append(Symbol(name=name, kind=symbol_kind_name(entry.get("kind")),
                               range=span,
                               container=parent if isinstance(parent, str) else container))
            continue
        span = _range(entry.get("selectionRange") or entry.get("range"))
        into.append(Symbol(name=name, kind=symbol_kind_name(entry.get("kind")),
                           range=span, container=container))
        _collect_symbols(entry.get("children"), name, into)


# --------------------------------------------------------------------------- #
# Reading the file the query is about
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SourceFile:
    """The resolved, in-workspace, readable file a query names."""

    path: str
    uri: str
    text: str


def read_source(file_path: str, workspace_root: str) -> SourceFile:
    """Resolve a query's file inside its workspace and read it, or say why not.

    Containment is checked after resolving symlinks, not before. A path that resolves
    outside the workspace is refused even though the language server would happily read
    it: the tool is a read of the project, and a workspace escape that only shows up in
    an LSP answer is a read nothing else in the pipeline would have allowed.
    """
    root = Path(workspace_root).expanduser()
    if not root.is_dir():
        raise LspError(f"the workspace root {workspace_root!r} is not a directory",
                       LspErrorCode.INVALID_REQUEST)
    root = root.resolve()
    candidate = Path(file_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()

    if resolved != root and root not in resolved.parents:
        raise LspError(f"{file_path!r} resolves outside the workspace {str(root)!r}",
                       LspErrorCode.INVALID_REQUEST)
    if not resolved.is_file():
        raise LspError(f"{file_path!r} is not a file", LspErrorCode.INVALID_REQUEST)
    size = resolved.stat().st_size
    if size > MAX_DOCUMENT_BYTES:
        raise LspError(f"{file_path!r} is {size} bytes, over the {MAX_DOCUMENT_BYTES}-byte "
                       f"limit this host opens", LspErrorCode.INVALID_REQUEST)
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise LspError(f"could not read {file_path!r} as utf-8 text: {error}",
                       LspErrorCode.INVALID_REQUEST) from error
    return SourceFile(path=str(resolved), uri=resolved.as_uri(), text=text)


# --------------------------------------------------------------------------- #
# The provider
# --------------------------------------------------------------------------- #
class StdioLspProvider(LspProvider):
    """Answers queries by running one language server per session and workspace.

    Per workspace because a language server indexes a root and answers about it. Per
    session because a session is what ends: `forget(session_id)` has to be able to name
    every process a run started, and a pool keyed only by workspace could not tell two
    concurrent runs' servers apart.
    """

    def __init__(self, *, id: str, command: Sequence[str],
                 extension_to_language: Dict[str, str],
                 env: Optional[Dict[str, str]] = None,
                 initialization_options: Any = None,
                 request_timeout: float = REQUEST_TIMEOUT,
                 initialize_timeout: float = INITIALIZE_TIMEOUT) -> None:
        if not command:
            raise LspError("a stdio LSP provider needs a command to run",
                           LspErrorCode.INVALID_PROVIDER)
        self.id = id
        self.extension_to_language = dict(extension_to_language)
        self._spec = ServerSpec(command=list(command), env=dict(env or {}),
                                initialization_options=initialization_options,
                                request_timeout=request_timeout,
                                initialize_timeout=initialize_timeout)
        self._servers: Dict[Tuple[str, str], LanguageServer] = {}
        self._pool_lock = threading.Lock()

    # -- answering -----------------------------------------------------

    def query(self, request: LspQuery) -> LspResult:
        """Read the file, find or start the server for its workspace, and ask.

        The source is read before the server is reached, so a bad path costs nothing and
        fails with the reason rather than as an empty result from a server that was
        handed a URI it could not open.
        """
        source = read_source(request.file_path, request.workspace_root)
        server = self._acquire(request.session_id, str(Path(request.workspace_root).resolve()))
        try:
            return server.query(request, source)
        except LspError as error:
            if error.code is not LspErrorCode.SERVER_FAILED or not self._retire(server):
                raise
            # The pooled process had already died — often it was reaped between two
            # queries. Retrying once on a fresh one turns a stale handle into a slow
            # answer instead of an error the agent has no way to act on.
            logger.info(f"| 🔎 Language server for {server.workspace_root} had died; retrying once")
            retry = self._acquire(request.session_id, server.workspace_root)
            return retry.query(request, source)

    def _acquire(self, session_id: str, workspace_root: str) -> LanguageServer:
        key = (session_id, workspace_root)
        with self._pool_lock:
            server = self._servers.get(key)
            if server is not None and server.alive:
                return server
            if server is not None:
                server.close()
                self._servers.pop(key, None)
            self._make_room(session_id)
            server = LanguageServer(self._spec, workspace_root)
            self._servers[key] = server
            return server

    def _make_room(self, session_id: str) -> None:
        """Hold the per-session server count, without dropping one that is answering.

        Unlike a terminal, a language server holds nothing the agent put there — closing
        an idle one costs the next query its warm-up and loses no state, so the cap
        evicts rather than refuses. One that has a query in flight is never touched: the
        caller is waiting on it, and killing it would return a failure for a question
        that was about to be answered.
        """
        mine = [(key, server) for key, server in self._servers.items() if key[0] == session_id]
        if len(mine) < MAX_SERVERS_PER_SESSION:
            return
        idle = sorted((entry for entry in mine if not entry[1].busy),
                      key=lambda entry: entry[1].last_used)
        if not idle:
            raise LspError(
                f"all {len(mine)} language servers in this session are busy; retry the query",
                LspErrorCode.UNAVAILABLE)
        for key, server in idle[:len(mine) - MAX_SERVERS_PER_SESSION + 1]:
            self._servers.pop(key, None)
            logger.info(f"| 🔎 Closing idle language server for {server.workspace_root}")
            server.close()

    def _retire(self, server: LanguageServer) -> bool:
        """Drop a dead server from the pool. False if something else already did."""
        with self._pool_lock:
            for key, pooled in list(self._servers.items()):
                if pooled is server:
                    self._servers.pop(key, None)
                    server.close()
                    return True
        return False

    # -- reaping -------------------------------------------------------

    def forget(self, session_id: str) -> None:
        with self._pool_lock:
            doomed = [(key, server) for key, server in self._servers.items()
                      if key[0] == session_id]
            for key, _ in doomed:
                self._servers.pop(key, None)
        for _, server in doomed:
            server.close()

    def close_all(self) -> None:
        with self._pool_lock:
            doomed = list(self._servers.values())
            self._servers.clear()
        for server in doomed:
            server.close()

    def live_servers(self) -> List[LanguageServer]:
        """Every server this provider currently holds. For tests and for reporting."""
        with self._pool_lock:
            return list(self._servers.values())


def default_python_provider() -> StdioLspProvider:
    """The bundled provider: `pylsp` over stdio, for `.py` and `.pyi`.

    One server, named, rather than a table of them. `pylsp` is the one this repo can
    reasonably expect: it installs with pip into the interpreter already running, and
    its engine, `jedi`, is already a dependency here.
    """
    from agentevolver.lsp.server import DEFAULT_PYTHON_COMMAND

    return StdioLspProvider(
        id="pylsp",
        command=[DEFAULT_PYTHON_COMMAND],
        extension_to_language={extension: "python" for extension in DEFAULT_PYTHON_EXTENSIONS},
    )


__all__ = [
    "CLIENT_CAPABILITIES",
    "DEFAULT_PYTHON_EXTENSIONS",
    "LanguageServer",
    "LspConnection",
    "MAX_SERVERS_PER_SESSION",
    "MessageDecoder",
    "ServerSpec",
    "SourceFile",
    "StdioLspProvider",
    "default_python_provider",
    "encode_message",
    "normalize_hover",
    "normalize_locations",
    "normalize_symbols",
    "read_source",
]
