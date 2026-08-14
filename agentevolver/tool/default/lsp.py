"""LspTool — ask a language server what a symbol is, instead of where its name appears.

Grep is the only way the agent currently has to find a symbol, and it answers a different
question than the one being asked. It finds the string. The definition, the call sites, and
the type are what an edit has to rest on, and none of them is a string match: `def close`
matches seven files, `close(` matches ninety, and the one that matters is the method on the
class the caller actually holds.

One tool with an operation parameter, not four tools. The four operations share a cursor, a
file, and a rendering; splitting them would put four nearly identical schemas in every
request for no decision the model gets to make differently. And the tool is always present:
with no language server installed it answers `LSP_UNAVAILABLE` and says what to use instead.
A tool that came and went with the machine would rewrite the prompt prefix, and the cached
prefix with it.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.config import config
from agentevolver.lsp import (
    Hover,
    Location,
    LspError,
    LspErrorCode,
    LspOperation,
    LspResult,
    Position,
    ResultKind,
    Symbol,
    lsp_manager,
)
from agentevolver.permission import Operation, PermissionRequest, permission_manager
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.sandbox.project import check_session_path
from agentevolver.tool.types import Tool, clip_output

_DESCRIPTION = ("Ask a language server for a symbol's definition, references, hover type, "
                "or a file's symbols.")

_INSTRUCTION = """
## Function
Answer a question about a symbol using the language server for that file, rather than by
matching text. Use it when a grep match is ambiguous, and before an edit whose safety
depends on knowing what a name refers to.

- `definition` — where the symbol under the cursor is defined. One place, usually.
- `references` — every use of it, including the declaration. This is the impact of a change.
- `hover` — its type and documentation, as the language server understands it.
- `symbols` — every symbol declared in the file, with its kind and line. No cursor needed.

## Guidance
- The cursor must be on the symbol. Point it at the name itself — the `f` of `foo`, not the
  space before it, not the `(` after it. An off-symbol position returns nothing.
- `line` and `character` count from 1, matching what read_file_tool shows you.
- An empty result means the server had nothing to say, not that nothing is there. A server
  still indexing, a dynamic attribute, or a position between symbols all look the same.
- If the answer is `LSP_UNAVAILABLE`, no language server handles that file type. That is
  final for this run — do not retry it. Use grep_search_tool and read_file_tool instead.
- `references` on a widely used name can be long; the result is capped and says so.

## Parameters
- operation (str): One of definition, references, hover, symbols.
- path (str): The file to ask about, absolute or relative to the workspace root.
- line (int, optional): One-based line of the cursor. Required for every operation
  except symbols.
- character (int, optional): One-based column of the cursor. Defaults to 1.

## Example
{"name": "lsp_tool", "args": {"operation": "definition", "path": "agentevolver/agent/types.py", "line": 412, "character": 9}}
{"name": "lsp_tool", "args": {"operation": "references", "path": "agentevolver/lsp/server.py", "line": 55, "character": 9}}
{"name": "lsp_tool", "args": {"operation": "symbols", "path": "agentevolver/lsp/stdio.py"}}
"""

#: Locations rendered before the rest are counted instead of listed. A `references` on a
#: common name can run to thousands; past a hundred the list stops being something to read
#: and becomes something to grep.
MAX_LOCATIONS = 100

#: Symbols rendered from one file. Above this the file is generated, and its outline is not
#: what the agent is looking for.
MAX_SYMBOLS = 200


def _render_uri(uri: str, workspace_uri: str) -> str:
    """A location as a path the agent can pass to read_file_tool.

    Relative to the workspace when it is inside it, absolute when it is not — a definition
    in an installed package is a real answer, and rewriting it as a relative path would
    point at a file that does not exist. A URI that is not a `file:` one is left alone;
    some servers answer with virtual documents that have no path at all.
    """
    if not uri.startswith("file://"):
        return uri
    if workspace_uri and uri.startswith(workspace_uri.rstrip("/") + "/"):
        return uri[len(workspace_uri.rstrip("/")) + 1:]
    from urllib.parse import unquote, urlparse

    return unquote(urlparse(uri).path) or uri


def _render_locations(locations: List[Location], workspace_uri: str) -> str:
    if not locations:
        return "No results. The server had nothing at that position — check the cursor is on the symbol."
    shown = locations[:MAX_LOCATIONS]
    lines = [f"{_render_uri(item.uri, workspace_uri)}:"
             f"{item.range.start.line + 1}:{item.range.start.character + 1}"
             for item in shown]
    omitted = len(locations) - len(shown)
    if omitted:
        lines.append(f"... and {omitted} more, not shown (limit {MAX_LOCATIONS}).")
    return "\n".join(lines)


def _render_hover(hover: Optional[Hover]) -> str:
    if hover is None:
        return "No hover information at that position."
    return hover.contents


def _render_symbols(symbols: List[Symbol]) -> str:
    if not symbols:
        return "No symbols in that file."
    shown = symbols[:MAX_SYMBOLS]
    lines = []
    for symbol in shown:
        name = f"{symbol.container}.{symbol.name}" if symbol.container else symbol.name
        lines.append(f"{symbol.range.start.line + 1}\t{symbol.kind}\t{name}")
    omitted = len(symbols) - len(shown)
    if omitted:
        lines.append(f"... and {omitted} more, not shown (limit {MAX_SYMBOLS}).")
    return "\n".join(lines)


def render(result: LspResult) -> str:
    """Turn one result into the text the model reads.

    Fails loudly on a kind it does not know. Python cannot force this to cover every arm
    the way a checked union does, and the alternative — falling through to an empty string
    — would read to the model as "there is no definition", which is the most expensive
    wrong answer this tool can give.
    """
    if result.kind is ResultKind.LOCATIONS:
        return _render_locations(result.locations, result.workspace_uri)
    if result.kind is ResultKind.HOVER:
        return _render_hover(result.hover)
    if result.kind is ResultKind.SYMBOLS:
        return _render_symbols(result.symbols)
    raise LspError(f"nothing renders an LSP result of kind {result.kind!r}",
                   LspErrorCode.MALFORMED_RESPONSE)


@TOOL.register_module(force=True)
class LspTool(Tool):
    """Query a language server for a definition, references, a type, or a file's symbols."""

    name: str = "lsp_tool"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={"canvas_category": "search"},
                                     description="The metadata of the tool")
    enable_evolving: bool = Field(default=False,
                                  description="Whether the tool may be evolved (self-optimized)")
    #: Reads a source file and asks a server about it. Nothing is written, no command from
    #: the model reaches the server, and `workspace/applyEdit` is refused if one asks —
    #: which is what makes the declaration below true rather than merely convenient.
    permission_mode: str = Field(default="read_only",
                                 description="Reads source and queries a language server.")
    mutates: bool = False
    #: Above the module's own initialize-plus-request budget, so a stalled server is
    #: reported by this tool, naming the request, rather than cut off by the pipeline.
    call_timeout_seconds: float = 90.0

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, operation: str, path: str, line: Optional[int] = None,
                       character: int = 1, **kwargs) -> Response:
        try:
            wanted = LspOperation(str(operation).strip().lower())
        except ValueError:
            return self._failed(
                f"{operation!r} is not an LSP operation. Use one of: "
                f"{', '.join(item.value for item in LspOperation)}.",
                LspErrorCode.INVALID_REQUEST)

        workspace = config.workspace_root or os.getcwd()
        target = str(path if os.path.isabs(str(path)) else Path(workspace) / str(path))

        denial = check_session_path(kwargs.get("ctx"), target, write=False)
        if denial:
            return self._failed(denial, LspErrorCode.INVALID_REQUEST)
        allowed = permission_manager.check(
            self.name, PermissionRequest(op=Operation.READ, target=target),
            workspace=workspace)
        if not allowed.allowed:
            return self._failed(f"Permission denied: {allowed.reason}",
                                LspErrorCode.INVALID_REQUEST)

        position, complaint = self._position(wanted, line, character)
        if complaint is not None:
            return complaint

        try:
            # Blocking: the query talks to a child process over pipes and waits. Off the
            # event loop, or every other action in the same batch waits with it.
            result = await asyncio.to_thread(
                lsp_manager.query, operation=wanted, file_path=target,
                workspace_root=workspace, position=position,
                session_id=str(getattr(kwargs.get("ctx"), "id", "") or ""))
        except LspError as error:
            return self._failed(error.message, error.code)
        except Exception as error:                                  # noqa: BLE001
            return self._failed(f"The language server query failed: {error}",
                                LspErrorCode.SERVER_FAILED)

        try:
            body = render(result)
        except LspError as error:
            return self._failed(error.message, error.code)

        where = f"{path}:{line}:{character}" if position is not None else str(path)
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"{wanted.value} — {where}\n\n{clip_output(body)}",
            data={"operation": wanted.value, "path": target, "kind": result.kind.value,
                  "count": len(result.locations) + len(result.symbols)},
        )

    @staticmethod
    def _position(operation: LspOperation, line: Optional[int], character: int):
        """The zero-based position, or the Response explaining why the cursor is unusable."""
        if not operation.needs_position:
            return None, None
        if line is None:
            return None, LspTool._failed(
                f"{operation.value} is a question about a symbol, so it needs a cursor: "
                f"pass line (and character) pointing at the name.",
                LspErrorCode.INVALID_REQUEST)
        try:
            one_based_line, one_based_character = int(line), int(character)
        except (TypeError, ValueError):
            return None, LspTool._failed(
                f"line and character must be whole numbers, not {line!r} and {character!r}.",
                LspErrorCode.INVALID_REQUEST)
        if one_based_line < 1 or one_based_character < 1:
            return None, LspTool._failed(
                f"line and character count from 1, as read_file_tool shows them; got "
                f"{one_based_line} and {one_based_character}.",
                LspErrorCode.INVALID_REQUEST)
        # The model counts from 1; the protocol counts from 0. Converted once, here.
        return Position(line=one_based_line - 1, character=one_based_character - 1), None

    @staticmethod
    def _failed(message: str, code: LspErrorCode) -> Response:
        """A failure the model can route on, not a sentence it has to parse.

        The code travels in `data` as well as in the message so a caller — a hook, a
        replay, a metric — can tell "no server for this language" from "that file does not
        exist" without matching on prose that will be reworded.
        """
        return Response(type=ResponseType.TOOL, success=False,
                        message=f"{code.value}: {message}",
                        data={"error_code": code.value})
