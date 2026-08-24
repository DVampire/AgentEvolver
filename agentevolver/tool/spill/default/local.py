"""The local spill store: private files under the machine-level runtime root."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from pathlib import Path
from typing import Optional

from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.tool.spill.types import SpillRef, SpillSource, SpillStore

#: Anything outside this becomes ``_``. Applied to the *suggested* name only, which
#: reaches us from a tool and therefore from arguments a model wrote.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

#: A suggested name is a label, not a filename budget. Long enough to stay readable
#: in a directory listing, short enough that the random prefix keeps the whole entry
#: well inside any filesystem's per-component limit.
_MAX_NAME = 64


def _safe_segment(suggested: str) -> str:
    """Reduce a caller's hint to one harmless path segment.

    ``Path(...).name`` first, so ``../../etc/passwd`` loses its directories before
    the character filter ever runs — a filter alone would happily pass ``....//``
    on some platforms.
    """
    base = Path(suggested).name
    cleaned = _UNSAFE.sub("_", base).lstrip(".")[:_MAX_NAME]
    return cleaned or "output.txt"


class LocalSpillStore(SpillStore):
    """Write spilled text to owner-only files under ``output/.runtime/spill``.

    Layout is ``<root>/<session-hash>/<random>-<safe-name>``. The session is hashed
    rather than used literally so a session id carrying a slash cannot climb out of
    the root, and the random prefix means two calls that suggest the same name never
    collide.

    Files are opened ``'x'`` (exclusive create) with mode ``0600`` under a ``0700``
    directory: a symlink planted at the target path makes the create fail instead of
    redirecting the write somewhere else.
    """

    name: str = "local_spill_store"

    async def save_text(
        self,
        content: str,
        source: SpillSource,
        *,
        session_key: Optional[str] = None,
        suggested_name: str = "output.txt",
    ) -> SpillRef:
        digest = hashlib.sha256((session_key or "shared").encode("utf-8")).hexdigest()[:16]
        directory = path_manager.get(P.SPILL) / f"session-{digest}"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

        path = directory / f"{secrets.token_hex(4)}-{_safe_segment(suggested_name)}"
        # 'x' rather than 'w': a pre-existing path — a planted symlink, or the
        # vanishingly unlikely token collision — is an error, never a redirect.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)

        logger.info(f"| 💾 Spilled {len(content):,} characters from {source.tool_name} to {path}")
        return SpillRef(
            locator=str(path),
            chars=len(content),
            retrieval_hint=(
                # Backticked, because the sentence continues after it: an unquoted path
                # followed by a full stop invites both a reader and a model to take the
                # stop as part of the filename.
                f"The full output is saved at `{path}`. Read it with `read_file_tool`, or "
                f"search it with `grep_search_tool` / `bash_tool` if you only need part of it."
            ),
        )


__all__ = ["LocalSpillStore"]
