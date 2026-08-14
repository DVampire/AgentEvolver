"""What an LSP query asks, what an answer may be, and what a provider owes the seam.

Nothing here is the wire protocol. The vocabulary is deliberately smaller than LSP —
four operations, three result shapes, one error type carrying a stable code — because
the schema the model sees is generated from it, and every field that reaches the model
is a field that must mean the same thing whichever language server answered. A provider
that wants to hand back something this file cannot name has to change this file, which
is the point: the model contract moves on purpose or not at all.

Positions here are zero-based UTF-16, matching the protocol. The one-based convention a
person and a model both expect belongs to the tool, which converts at the edge — keeping
the conversion in one place, rather than in each provider, is what stops an off-by-one
from becoming a per-language quirk.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class LspOperation(str, Enum):
    """The four questions the seam can ask.

    A closed set, not a passthrough. A generic "send this JSON-RPC method" escape hatch
    would let a caller reach `workspace/executeCommand` — which runs code and edits
    files — through a tool that declares itself read-only.
    """

    DEFINITION = "definition"
    REFERENCES = "references"
    HOVER = "hover"
    SYMBOLS = "symbols"

    @property
    def needs_position(self) -> bool:
        """Whether the query is about a cursor. Symbols are about the whole file."""
        return self is not LspOperation.SYMBOLS


class LspErrorCode(str, Enum):
    """Stable codes a caller routes on instead of matching against a message.

    ``UNAVAILABLE`` is the one the model sees most: it means the query was well formed
    and no language server could answer it. It is a result, not a crash — the tool stays
    in the prompt and says so, rather than disappearing and rewriting the cached prefix.
    """

    UNAVAILABLE = "LSP_UNAVAILABLE"
    INVALID_PROVIDER = "LSP_INVALID_PROVIDER"
    CONFLICT = "LSP_CONFLICT"
    INVALID_REQUEST = "LSP_INVALID_REQUEST"
    UNSUPPORTED_OPERATION = "LSP_UNSUPPORTED_OPERATION"
    MALFORMED_RESPONSE = "LSP_MALFORMED_RESPONSE"
    SERVER_FAILED = "LSP_SERVER_FAILED"
    TIMEOUT = "LSP_TIMEOUT"


class LspError(RuntimeError):
    """A failed query, carrying the code that says which kind of failure it was."""

    def __init__(self, message: str, code: LspErrorCode) -> None:
        super().__init__(message)
        self.code = code

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else ""

    def __str__(self) -> str:
        return f"{self.code.value}: {self.message}"


@dataclass(frozen=True)
class Position:
    """A zero-based line and UTF-16 column, as the protocol counts them."""

    line: int
    character: int

    def as_wire(self) -> Dict[str, int]:
        return {"line": self.line, "character": self.character}


@dataclass(frozen=True)
class Range:
    """A half-open span `[start, end)` inside one document."""

    start: Position
    end: Position


@dataclass(frozen=True)
class Location:
    """One place a symbol is: a document URI and the span within it.

    The URI is kept verbatim from the server rather than turned into a path here. A
    language server may answer with a location inside a dependency, a zip, or a virtual
    document, and rewriting those into host paths invents files that do not exist.
    """

    uri: str
    range: Range


@dataclass(frozen=True)
class Hover:
    """What the server says about the symbol under the cursor."""

    contents: str
    range: Optional[Range] = None


@dataclass(frozen=True)
class Symbol:
    """One named thing in a file, flattened out of whatever shape the server sent.

    `container` is the enclosing name — the class a method belongs to — and is what
    makes a flat list readable: three entries called `close` are indistinguishable
    without it.
    """

    name: str
    kind: str
    range: Range
    container: str = ""


class ResultKind(str, Enum):
    """Which of the three answer shapes a result carries."""

    LOCATIONS = "locations"
    HOVER = "hover"
    SYMBOLS = "symbols"


@dataclass(frozen=True)
class LspResult:
    """One answer. Exactly one of the three payloads is meaningful, named by `kind`.

    Python cannot make a caller handle every arm the way a checked union does, so the
    renderer that reads this fails loudly on a kind it does not know instead of quietly
    returning an empty answer — a silent empty result reads to the model as "no
    definition exists", which is the most expensive wrong answer this module can give.

    `workspace_uri` is the provider's own URI for the workspace root it resolved. A
    caller that wants workspace-relative paths compares against this rather than against
    the request's root: the server may have canonicalized a symlink, and comparing
    against the uncanonicalized root makes every location look external.
    """

    kind: ResultKind
    locations: List[Location] = field(default_factory=list)
    hover: Optional[Hover] = None
    symbols: List[Symbol] = field(default_factory=list)
    workspace_uri: str = ""


@dataclass(frozen=True)
class LspQuery:
    """A resolved query, as a provider receives it.

    Every field is filled in by the seam before a provider sees it, including
    `language_id`, which comes from the provider's own extension table rather than from
    the caller. A caller that could name the language could name one the server does not
    speak, and the failure would surface as an empty result rather than as a refusal.
    """

    operation: LspOperation
    file_path: str
    workspace_root: str
    position: Optional[Position] = None
    language_id: str = ""
    session_id: str = ""


class LspProvider(ABC):
    """A backend that can answer queries for some set of file extensions.

    A provider owns processes; the seam owns routing. That split is why `forget` and
    `close_all` are part of the contract rather than an implementation detail: the run
    that started a language server ends in `Agent._release_session_resources`, which
    knows a session id and nothing about subprocesses.
    """

    #: Stable identity, reserved on registration. Also what a conflict names.
    id: str = ""

    #: Lowercase, leading-dot extension to LSP language id, e.g. `{".py": "python"}`.
    extension_to_language: Dict[str, str] = {}

    @abstractmethod
    def query(self, query: LspQuery) -> LspResult:
        """Answer one query. Blocking; the tool runs it off the event loop thread."""

    def forget(self, session_id: str) -> None:
        """Shut down every server this provider started for one session."""

    def close_all(self) -> None:
        """Shut down every server this provider started, for every session."""


#: LSP `SymbolKind`, which is sent as a bare integer. Rendering `12` to the model instead
#: of `function` costs a lookup the model cannot perform.
SYMBOL_KINDS: Dict[int, str] = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant", 15: "string",
    16: "number", 17: "boolean", 18: "array", 19: "object", 20: "key",
    21: "null", 22: "enum-member", 23: "struct", 24: "event", 25: "operator",
    26: "type-parameter",
}


def symbol_kind_name(kind: object) -> str:
    """Name a wire `SymbolKind`, keeping an unknown one visible rather than dropping it."""
    if isinstance(kind, int) and kind in SYMBOL_KINDS:
        return SYMBOL_KINDS[kind]
    return f"kind-{kind}"


def normalize_extension(extension: str) -> str:
    """Lowercase an extension and give it a leading dot; `.PY` and `py` are the same route."""
    lowered = extension.strip().lower()
    return lowered if lowered.startswith(".") else f".{lowered}"


def final_extension(file_path: str) -> str:
    """The last extension of a path, normalized; `""` when there is none.

    A dotfile has no extension: `.bashrc` is a name, not a `.bashrc` file. Splitting on
    both separators keeps a caller's path style from changing which provider answers.
    """
    cut = max(file_path.rfind("/"), file_path.rfind("\\"))
    base = file_path[cut + 1:] if cut >= 0 else file_path
    dot = base.rfind(".")
    if dot <= 0:
        return ""
    return base[dot:].lower()


__all__ = [
    "Hover",
    "Location",
    "LspError",
    "LspErrorCode",
    "LspOperation",
    "LspProvider",
    "LspQuery",
    "LspResult",
    "Position",
    "Range",
    "ResultKind",
    "SYMBOL_KINDS",
    "Symbol",
    "final_extension",
    "normalize_extension",
    "symbol_kind_name",
]
