"""LspServer — the seam the tool talks to, and the only thing that knows which provider answers.

Routing lives here so that provider choice never reaches the model. The tool asks for a
definition in a file; this decides that `.py` is answered by the Python provider and that
the language id is `python`. Add a Go server tomorrow and the tool's schema, its
description, and therefore the cached prompt prefix are all unchanged — which is the
whole reason the seam exists rather than the tool spawning a server itself.

The other half of its job is reaping. A language server is a subprocess that outlives the
query that started it on purpose: the second query is fast because the first one paid for
indexing. Nothing else in the process knows those children exist, so every way a run can
end goes through `forget`, the same way terminals and jobs do.
"""

from __future__ import annotations

import atexit
import shutil
from typing import Dict, List, Optional, Tuple

from agentevolver.logger import logger
from agentevolver.lsp.types import (
    LspError,
    LspErrorCode,
    LspOperation,
    LspProvider,
    LspQuery,
    LspResult,
    Position,
    final_extension,
    normalize_extension,
)
from agentevolver.utils import Singleton

#: The Python language server this repo ships a provider for. Chosen because it installs
#: with `pip install python-lsp-server` into the same interpreter the framework already
#: runs on, needs no node or rust toolchain, and is built on `jedi`, which is already a
#: dependency here. One server, named, rather than a catalog nobody can keep true.
DEFAULT_PYTHON_COMMAND = "pylsp"

_INSTALL_HINT = (f"Install one with `pip install python-lsp-server` "
                 f"(it provides `{DEFAULT_PYTHON_COMMAND}`).")


class LspServer(metaclass=Singleton):
    """Holds the providers, routes one query to one of them, and reaps what they started."""

    def __init__(self) -> None:
        self._providers: Dict[str, LspProvider] = {}
        #: extension -> (provider id, language id). One provider owns an extension
        #: exclusively, so routing does not depend on registration order.
        self._routes: Dict[str, Tuple[str, str]] = {}
        self._atexit_registered = False
        self._default_attempted = False

    # ------------------------------------------------------------------
    # Registering
    # ------------------------------------------------------------------

    def register_provider(self, provider: LspProvider) -> None:
        """Reserve a provider's id and all of its extensions, or change nothing.

        Checked before anything is written. A half-registered provider owns some of its
        extensions and not others, so a `.py` query reaches a server that was never
        initialized while a `.pyi` query reports nothing available — one failure that
        looks like two unrelated ones.
        """
        provider_id = (provider.id or "").strip()
        if not provider_id:
            raise LspError("an LSP provider needs a non-empty id",
                           LspErrorCode.INVALID_PROVIDER)
        if provider_id in self._providers:
            raise LspError(f"an LSP provider with id {provider_id!r} is already registered",
                           LspErrorCode.CONFLICT)

        mapping = dict(provider.extension_to_language or {})
        if not mapping:
            raise LspError(f"LSP provider {provider_id!r} claims no file extensions",
                           LspErrorCode.INVALID_PROVIDER)

        pending: Dict[str, Tuple[str, str]] = {}
        for raw_extension, language_id in mapping.items():
            extension = normalize_extension(str(raw_extension))
            if len(extension) < 2 or "." in extension[1:] or "/" in extension:
                raise LspError(
                    f"LSP provider {provider_id!r} claims an unusable extension "
                    f"{raw_extension!r}", LspErrorCode.INVALID_PROVIDER)
            if not str(language_id).strip():
                raise LspError(
                    f"LSP provider {provider_id!r} maps {extension} to an empty language id",
                    LspErrorCode.INVALID_PROVIDER)
            if extension in pending:
                raise LspError(
                    f"LSP provider {provider_id!r} claims {extension} twice",
                    LspErrorCode.INVALID_PROVIDER)
            pending[extension] = (provider_id, str(language_id))

        taken = sorted(set(pending) & set(self._routes))
        if taken:
            holder = self._routes[taken[0]][0]
            raise LspError(
                f"{', '.join(taken)} already routes to LSP provider {holder!r}",
                LspErrorCode.CONFLICT)

        self._providers[provider_id] = provider
        self._routes.update(pending)
        self._arm_process_exit()
        logger.info(f"| 🔎 LSP provider {provider_id} handles {', '.join(sorted(pending))}")

    def unregister_provider(self, provider_id: str) -> bool:
        """Drop a provider, its routes, and its servers. False if it was not registered."""
        provider = self._providers.pop(provider_id, None)
        if provider is None:
            return False
        for extension in [ext for ext, route in self._routes.items() if route[0] == provider_id]:
            self._routes.pop(extension, None)
        self._shutdown(provider, provider.close_all, "close")
        return True

    # ------------------------------------------------------------------
    # Asking
    # ------------------------------------------------------------------

    def query(self, *, operation: LspOperation, file_path: str, workspace_root: str,
              position: Optional[Position] = None, session_id: str = "") -> LspResult:
        """Route one query by the file's extension and run it.

        Raises `LspError` with `LSP_UNAVAILABLE` when nothing handles the file. That is a
        report, not an outage: the caller is meant to say so and carry on with grep,
        which is what it would have done anyway.
        """
        self._ensure_default_provider()

        extension = final_extension(file_path)
        route = self._routes.get(extension)
        if route is None:
            raise LspError(self._unavailable_reason(file_path, extension),
                           LspErrorCode.UNAVAILABLE)
        provider_id, language_id = route
        provider = self._providers[provider_id]

        if operation.needs_position and position is None:
            raise LspError(f"{operation.value} is a question about a cursor and needs a position",
                           LspErrorCode.INVALID_REQUEST)

        return provider.query(LspQuery(
            operation=operation, file_path=file_path, workspace_root=workspace_root,
            position=position, language_id=language_id, session_id=session_id))

    def handled_extensions(self) -> List[str]:
        """Every extension some provider answers for, sorted."""
        self._ensure_default_provider()
        return sorted(self._routes)

    def _unavailable_reason(self, file_path: str, extension: str) -> str:
        """Say what is missing and what to do, because "unavailable" alone is a dead end.

        An agent told only that LSP is unavailable will either retry the same call or
        assume the symbol does not exist. Both are worse than being told to grep.
        """
        handled = sorted(self._routes)
        subject = f"{extension} files" if extension else f"{file_path!r}, which has no extension"
        if not handled:
            return (f"No language server is registered, so nothing can answer for {subject}. "
                    f"{_INSTALL_HINT} Until then, use grep_search_tool and read_file_tool.")
        return (f"No language server handles {subject}. Registered extensions: "
                f"{', '.join(handled)}. Use grep_search_tool for this file instead.")

    # ------------------------------------------------------------------
    # Reaping
    # ------------------------------------------------------------------

    def forget(self, session_id: str) -> None:
        """End every language server a session started.

        Called from `Agent._release_session_resources` at the end of a run, beside the
        job and terminal registries. Without it a finished session leaves an indexing
        process holding a workspace that no longer belongs to anyone, and in a long-lived
        host nothing else ever collects it — `atexit` does not fire in a gateway that
        stays up for a week.
        """
        for provider in list(self._providers.values()):
            self._shutdown(provider, lambda p=provider: p.forget(session_id), "release")

    def close_all(self) -> None:
        """End every language server. Registrations stay: they are configuration, not state."""
        for provider in list(self._providers.values()):
            self._shutdown(provider, provider.close_all, "close")

    @staticmethod
    def _shutdown(provider: LspProvider, action, verb: str) -> None:
        """Run a teardown step without letting one provider's failure strand the others."""
        try:
            action()
        except Exception as error:                                  # noqa: BLE001
            logger.warning(f"| ⚠️ Could not {verb} LSP provider {provider.id}: {error}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _arm_process_exit(self) -> None:
        """Kill language servers when the process ends, and register the hook only once."""
        if self._atexit_registered:
            return
        atexit.register(self.close_all)
        self._atexit_registered = True

    def _ensure_default_provider(self) -> None:
        """Register the bundled Python provider once, if its server is installed.

        Attempted lazily rather than at import, because looking for an executable on
        PATH is not something importing a module should do, and because a process that
        never queries should never own a language server.

        An explicitly registered provider wins: if something already claims `.py`, this
        stays out of the way rather than raising a conflict at query time from a
        registration nobody asked for.
        """
        if self._default_attempted:
            return
        self._default_attempted = True

        from agentevolver.lsp.stdio import DEFAULT_PYTHON_EXTENSIONS, default_python_provider

        if any(extension in self._routes for extension in DEFAULT_PYTHON_EXTENSIONS):
            return
        if shutil.which(DEFAULT_PYTHON_COMMAND) is None:
            logger.info(f"| 🔎 No {DEFAULT_PYTHON_COMMAND} on PATH; LSP answers "
                        f"{LspErrorCode.UNAVAILABLE.value} until one is installed")
            return
        try:
            self.register_provider(default_python_provider())
        except LspError as error:                                   # noqa: PERF203
            logger.warning(f"| ⚠️ Could not register the default LSP provider: {error}")


lsp_manager = LspServer()

__all__ = ["DEFAULT_PYTHON_COMMAND", "LspServer", "lsp_manager"]
