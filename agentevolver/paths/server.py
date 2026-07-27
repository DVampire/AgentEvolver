"""Path manager: the single authority on where anything is written.

Every path the framework writes is declared in :data:`LAYOUT` and resolved
through this manager, so the whole disk contract is one table instead of joins
scattered across the gateway, the sandbox, the IDE and half a dozen others.
Moving a directory becomes a one-line edit.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

from agentevolver.paths.types import LAYOUT, P

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class PathManagerServer:
    """Resolve layout keys to absolute paths, and answer questions about the tree."""

    def __init__(self) -> None:
        self._layout: Dict[P, str] = dict(LAYOUT)

    # ------------------------------------------------------------- roots
    @staticmethod
    def project_dir() -> Path:
        """The directory the tree hangs off — the current project, or an override.

        ``AGENTEVOLVER_HOME`` keeps its historical meaning: point the whole tree
        somewhere else (a shared volume, a scratch disk).
        """
        override = os.environ.get("AGENTEVOLVER_HOME")
        return Path(override).expanduser().resolve() if override else Path.cwd().resolve()

    def writable_roots(self) -> Tuple[Path, Path]:
        """The only two directories the framework may write to.

        Exposed so tests can assert the rule rather than trusting convention:
        ``output/`` for generated, machine- and user-specific state, and
        ``extension/`` for shared, durable components.
        """
        return self.get(P.OUTPUT), self.get(P.EXTENSION)

    # ---------------------------------------------------------- resolution
    def get(self, key: P, *, create: bool = False, **params: str) -> Path:
        """Resolve a layout key to an absolute path.

        Args:
            key: Which path, from :class:`~agentevolver.paths.types.P`.
            create: Create the directory (or the parent, for a file key) first.
            **params: Values for the template's placeholders, e.g. ``owner``.

        Raises:
            KeyError: The key has no entry in the layout.
            ValueError: A placeholder the template needs was not supplied —
                raised here rather than silently producing a directory literally
                named ``{owner}``, which is painful to trace back.
        """
        try:
            template = self._layout[key]
        except KeyError:
            raise KeyError(f"No layout entry for {key!r}") from None

        required = set(_PLACEHOLDER.findall(template))
        missing = required - params.keys()
        if missing:
            raise ValueError(f"{key.value} needs {sorted(missing)}; got {sorted(params)}")

        path = self.project_dir() / template.format(**params)
        if create:
            (path.parent if path.suffix else path).mkdir(parents=True, exist_ok=True)
        return path

    def params_for(self, key: P) -> List[str]:
        """Placeholders a key needs — useful for callers building paths generically."""
        return sorted(set(_PLACEHOLDER.findall(self._layout[key])))

    def describe(self) -> Dict[str, str]:
        """The whole layout as plain data, for logging and diagnostics."""
        return {key.value: template for key, template in self._layout.items()}


# Global path manager instance
path_manager = PathManagerServer()

__all__ = ["PathManagerServer", "path_manager"]
