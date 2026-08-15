"""Run one model-written program in its own interpreter, with named callbacks into ours.

This is the substrate and nothing else: it is handed a program and a dict of async
functions, it runs the program in a child process, and every time the program calls one
of those names it calls the function here and sends the answer back. It does not know
what a tool is, what a permission is, or who is asking — which is what keeps the
guarded-dispatch decision in one place (``agentevolver/tool/default/code_mode/``) instead
of half here.

**The isolation this buys, precisely.** A fresh process per run, so no state carries
across runs and nothing the program does can touch the objects in the agent's process —
notably the permission manager and the hook registry. That is the property being bought.
It is not a security boundary: the child runs as the same user with the same filesystem,
exactly like ``bash_tool``, which already runs arbitrary model-written commands here. A
deployment that needs a real boundary needs one for bash first.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.code import bootstrap as _bootstrap
from agentevolver.code.types import (
    MAX_LOG_CHARS,
    CodeFailure,
    CodeFailureType,
    CodeRunResult,
)
from agentevolver.logger import logger

#: The script the child interpreter runs. Taken from the module object rather than
#: assembled from a directory, so moving the package cannot leave a stale path behind.
_BOOTSTRAP = os.path.abspath(_bootstrap.__file__)

#: How much of one message the host will read before treating the channel as broken.
#: Larger than the child's own per-line cap so a legal line always fits.
_STREAM_LIMIT = 1_048_576

#: How long to wait for the child to exit after it has reported its result, in seconds.
#: It has already said everything it is going to say; this only covers interpreter
#: teardown, and a child that will not leave is killed rather than waited on.
_EXIT_GRACE_SECONDS = 5.0


class CodeRuntimeServer(BaseModel):
    """Runs programs in child interpreters and bridges their calls back here."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    max_log_chars: int = Field(default=MAX_LOG_CHARS, description="Ceiling on captured output, in characters.")

    async def run(
        self,
        program: str,
        bindings: Optional[Dict[str, Callable[[Dict[str, Any]], Awaitable[Any]]]] = None,
        *,
        timeout: float = 600.0,
        workspace: Optional[str] = None,
        max_parallel: int = 8,
    ) -> CodeRunResult:
        """Execute ``program``, letting it call ``bindings`` by name.

        Args:
            program: The body of an async function, as the model wrote it.
            bindings: Name -> async callable taking the program's keyword arguments as
                one dict. A binding that raises becomes a program-visible failure of that
                call, not a failure of the run.
            timeout: Wall-clock budget for the whole run, in seconds.
            workspace: Working directory for the child, so relative paths in the program
                mean what they mean to the agent's shell.
            max_parallel: How many bound calls may be in flight at once.

        Returns:
            CodeRunResult: what the program printed, returned, and how it ended.
        """
        bindings = bindings or {}
        cwd = os.path.abspath(workspace) if workspace and os.path.isdir(workspace) else None
        # `-I` keeps the bootstrap's own directory off `sys.path`. Without it the child's
        # first import wins from beside the script, and this package ships a `types.py` —
        # the interpreter dies inside `enum` before reaching a single line of the program.
        # The bootstrap puts the workspace back on the path itself, at the end, where it
        # can be imported from without shadowing the standard library.
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-u", _BOOTSTRAP,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            limit=_STREAM_LIMIT,
        )
        # Drained from the start rather than read at the end: the child only writes here
        # when it dies before it can report, and a diagnostic that fills the pipe buffer
        # would deadlock the very run it is trying to explain.
        errors = asyncio.create_task(process.stderr.read())
        state = _RunState(self.max_log_chars)
        expired = False
        try:
            result = await asyncio.wait_for(
                self._converse(process, program, bindings, state, max_parallel), timeout
            )
        except asyncio.TimeoutError:
            expired = True
            # The conversation coroutine was cancelled where it stood, which does not
            # reach the calls it started — those are separate tasks and would otherwise
            # outlive the run they belong to.
            for task in state.in_flight:
                task.cancel()
            result = CodeRunResult(
                logs=state.logs,
                calls=state.calls,
                failure=CodeFailure(
                    type=CodeFailureType.TIMEOUT,
                    message=(
                        f"The program was stopped after {timeout:g}s and its process killed. "
                        f"Anything it printed before then is above. Split the work, or give "
                        f"the slow part its own program."
                    ),
                ),
            )
        finally:
            await self._reap(process, errors, kill=expired)

        if result.failure is not None and result.failure.type is CodeFailureType.RUNTIME_EXIT:
            diagnostic = (errors.result() if errors.done() and not errors.cancelled() else b"")
            detail = diagnostic.decode("utf-8", errors="replace").strip()
            if detail:
                result.failure.message = f"{result.failure.message}\n{detail}"
        return result

    async def _converse(
        self,
        process: asyncio.subprocess.Process,
        program: str,
        bindings: Dict[str, Callable[[Dict[str, Any]], Awaitable[Any]]],
        state: "_RunState",
        max_parallel: int,
    ) -> CodeRunResult:
        """Send the program, serve its calls, and settle when it reports done."""
        process.stdin.write(
            json.dumps({"program": program, "bindings": sorted(bindings)}, ensure_ascii=False).encode()
            + b"\n"
        )
        await process.stdin.drain()

        gate = asyncio.Semaphore(max(1, int(max_parallel)))
        writing = asyncio.Lock()
        in_flight = state.in_flight
        value: Any = None
        failure: Optional[CodeFailure] = None
        settled = False

        while True:
            try:
                line = await process.stdout.readline()
            except ValueError:
                # One message longer than the reader's limit. The child caps what it
                # sends, so this is the program writing to the channel behind its back:
                # the stream is no longer parseable and the run cannot be settled from it.
                break
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                # The peer runs model code. A program that wrote to fd 1 itself put
                # something here that is not part of the protocol; it is not a reason to
                # fail the run, and it is not something to act on either.
                continue
            message_type = message.get("t")
            if message_type == "log":
                state.log(str(message.get("text", "")))
            elif message_type == "call":
                state.calls += 1
                in_flight.append(asyncio.create_task(
                    self._serve_call(process, message, bindings, gate, writing)
                ))
            elif message_type == "done":
                value = message.get("value")
                raw = message.get("failure")
                if raw:
                    failure = CodeFailure(
                        type=CodeFailureType(raw.get("type", "exception")),
                        message=str(raw.get("message", "")),
                    )
                settled = True
                break

        # Calls still running when the program returned are finished, not cancelled: each
        # one is a real tool call halfway through its own hook pair, and abandoning it
        # would leave a started action with no recorded result.
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)

        if not settled:
            failure = CodeFailure(
                type=CodeFailureType.RUNTIME_EXIT,
                message="The interpreter running the program exited before it reported a result.",
            )
        return CodeRunResult(value=value, logs=state.logs, failure=failure, calls=state.calls)

    async def _serve_call(
        self,
        process: asyncio.subprocess.Process,
        message: Dict[str, Any],
        bindings: Dict[str, Callable[[Dict[str, Any]], Awaitable[Any]]],
        gate: asyncio.Semaphore,
        writing: asyncio.Lock,
    ) -> None:
        """Run one bound call and reply to the child."""
        name = str(message.get("name", ""))
        args = message.get("args")
        reply: Dict[str, Any] = {"t": "reply", "id": message.get("id")}
        try:
            binding = bindings.get(name)
            if binding is None:
                available = ", ".join(sorted(bindings)) or "none"
                raise LookupError(f"No binding named {name!r}. Callable from a program: {available}.")
            if not isinstance(args, dict):
                raise TypeError(f"{name} takes keyword arguments; got {type(args).__name__}.")
            reply["value"] = await binding(args)
            reply["ok"] = True
        except Exception as error:  # noqa: BLE001 — the program decides what to do about it
            reply["ok"] = False
            reply["message"] = str(error) or error.__class__.__name__
        async with writing:
            try:
                process.stdin.write(json.dumps(reply, ensure_ascii=False, default=str).encode() + b"\n")
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                # The program ended while this call was in flight. Its result has nowhere
                # to go, which is the program's choice, not an error in the run.
                logger.debug(f"| 🧩 code runtime: reply to {name} dropped, the program had ended")

    @staticmethod
    async def _reap(process: asyncio.subprocess.Process, errors: asyncio.Task, *, kill: bool) -> None:
        """Leave no child behind, however the run ended.

        ``kill`` skips the grace period. A run that expired has already spent its whole
        budget, and the program that overran it is the one least likely to exit politely —
        waiting another five seconds on it means the timeout the model was promised is not
        the timeout it gets.
        """
        if process.returncode is None:
            if kill:
                process.kill()
                await process.wait()
            else:
                try:
                    process.stdin.close()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # already gone; the wait below settles it
                try:
                    await asyncio.wait_for(process.wait(), _EXIT_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        try:
            await asyncio.wait_for(errors, _EXIT_GRACE_SECONDS)
        except asyncio.TimeoutError:
            errors.cancel()


class _RunState:
    """Captured output for one run, bounded as it arrives."""

    def __init__(self, limit: int) -> None:
        self.logs: List[str] = []
        self.calls = 0
        #: Bound calls started and not yet finished, held here so a run that is
        #: abandoned at its time budget can still reach them.
        self.in_flight: List[asyncio.Task] = []
        self._limit = limit
        self._size = 0
        self._elided = 0

    def log(self, line: str) -> None:
        if self._size >= self._limit:
            self._elided += 1
            if self._elided == 1:
                self.logs.append(
                    f"[output stopped at {self._limit:,} characters; later lines were dropped. "
                    f"Print less, or write the bulk to a file and read the part you need.]"
                )
            return
        self.logs.append(line)
        self._size += len(line) + 1


#: Global code runtime instance.
code_runtime = CodeRuntimeServer()

__all__ = ["CodeRuntimeServer", "code_runtime"]
