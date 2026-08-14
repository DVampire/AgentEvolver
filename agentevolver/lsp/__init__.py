"""Language-server queries: what a symbol is, not where its name appears."""

from .server import DEFAULT_PYTHON_COMMAND, LspServer, lsp_manager
from .stdio import (
    MAX_SERVERS_PER_SESSION,
    LanguageServer,
    StdioLspProvider,
    default_python_provider,
    read_source,
)
from .types import (
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
    final_extension,
    normalize_extension,
    symbol_kind_name,
)

__all__ = [
    "DEFAULT_PYTHON_COMMAND",
    "Hover",
    "LanguageServer",
    "Location",
    "LspError",
    "LspErrorCode",
    "LspOperation",
    "LspProvider",
    "LspQuery",
    "LspResult",
    "LspServer",
    "MAX_SERVERS_PER_SESSION",
    "Position",
    "Range",
    "ResultKind",
    "StdioLspProvider",
    "Symbol",
    "default_python_provider",
    "final_extension",
    "lsp_manager",
    "normalize_extension",
    "read_source",
    "symbol_kind_name",
]
