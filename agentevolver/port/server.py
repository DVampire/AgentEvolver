"""Central port registry.

One place that (a) names the framework's well-known default ports and (b) hands
out and records host ports, so dynamic bindings (deploy sites, the Gateway)
are de-conflicted and discoverable instead of being ad-hoc literals scattered
across the codebase.

Allocations are persisted to ``output/.runtime/ports.json`` (``P.PORTS``),
so every process and every run sees the same picture of what is bound where.

"Every process" is the load-bearing claim, and it was false. Each `PortManager`
loaded the file once into an in-memory dict and mutated that; a second framework
instance — concurrent ProgramBench runs, a gateway beside a script — never saw the
first's writes, and whichever saved last overwrote the other's registrations. Reads
returned whatever that stale cache held. Every operation now reads the file fresh and
writes it back under a cross-process lock, so the picture is genuinely shared.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

from agentevolver.paths import P, path_manager
from agentevolver.logger import logger
from agentevolver.utils.file_utils import atomic_json_update
from agentevolver.utils.path_utils import home_dir

# --- Well-known framework HOST service ports (single source of truth) --------
# Only host-bound, framework-level services belong here. Ports that are internal
# to a specific environment/sandbox (e.g. a browser container's CDP/VNC ports)
# belong to that environment, not this global registry — they are fixed inside
# the container and mapped to ephemeral host ports by the opensandbox proxy, so
# they never collide on the host and never go through the PortManager.
GATEWAY = 9876          # Gateway WebSocket server
OPENSANDBOX = 8080      # local opensandbox-server daemon
UI = 5173               # Vite dev server for the web UI (single-port reverse proxy)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_free(port: int, host: str = "127.0.0.1") -> bool:
    """True if ``port`` can currently be bound on ``host``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def os_free_port(host: str = "127.0.0.1") -> int:
    """Ask the OS for an unused port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


class PortManager:
    """Reserve, record, and look up host ports, persisted to ``ports.json``."""

    def __init__(self) -> None:
        # An in-process lock still, so coroutines/threads in *this* process do not race
        # each other into the cross-process one needlessly. Cross-process exclusion is
        # `atomic_json_update`'s job; this just keeps one process orderly.
        self._lock = threading.RLock()
        self._path: Optional[str] = None

    def _registry_path(self) -> str:
        if self._path is None:
            self._path = str(path_manager.get(P.PORTS, create=True))
        return self._path

    def _read(self) -> Dict[str, dict]:
        """The registry as it is on disk right now. No cache — a second process may have
        written since the last call, and returning a stale port is how two runs collide."""
        try:
            data = json.loads(path_manager.get(P.PORTS, create=True).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def register(
        self,
        name: str,
        port: Optional[int] = None,
        *,
        preferred: Optional[int] = None,
        type: str = "host",
        override: bool = False,
    ) -> dict:
        """Register a port under ``name`` and persist it. Returns the record.

        Every port the framework uses — framework host services, dynamically
        allocated deploy ports, and an environment's own ports — registers here
        so the whole system is visible and de-conflicted in one place.

        - ``port`` given: register that exact binding (a known/fixed port, e.g.
          the Gateway's, or an environment's resolved host port). Always refreshed.
        - ``port`` omitted: allocate one — ``preferred`` if free, else an
          OS-assigned free port. Idempotent per ``name`` (reused across calls and
          runs) unless ``override`` is set.
        - ``type`` labels the entry (``host`` | ``env`` | …) for readability.

        The whole decision — is ``name`` already registered, is ``preferred`` free,
        what to store — happens inside one cross-process transaction, so two instances
        allocating at once cannot both pick the same OS-free port between the check and
        the write.
        """
        chosen: dict = {}

        def _mutate(registry):
            registry = registry if isinstance(registry, dict) else {}
            existing = registry.get(name)
            actual = port
            if actual is None:
                if existing and not override and isinstance(existing.get("port"), int):
                    chosen.update(existing)
                    return registry
                actual = preferred if (preferred and is_free(preferred)) else os_free_port()
            record = {
                "name": name,
                "port": actual,
                "preferred": preferred if preferred is not None else actual,
                "type": type,
                "pid": os.getpid(),
                "updated_at": _now(),
            }
            registry[name] = record
            chosen.update(record)
            return registry

        with self._lock:
            atomic_json_update(self._registry_path(), _mutate, default={})
        logger.info(f"| 🔌 Port registered: {name} -> {chosen.get('port')} ({type})")
        return chosen

    def unregister(self, name: str) -> bool:
        """Drop ``name``'s registration (its port becomes reusable). Returns whether it existed."""
        existed = {"v": False}

        def _mutate(registry):
            registry = registry if isinstance(registry, dict) else {}
            existed["v"] = registry.pop(name, None) is not None
            return registry

        with self._lock:
            atomic_json_update(self._registry_path(), _mutate, default={})
        return existed["v"]

    def get(self, name: str) -> Optional[int]:
        """Return the port registered under ``name``, or None."""
        with self._lock:
            rec = self._read().get(name)
            return rec.get("port") if rec else None

    def get_info(self, name: str) -> Optional[dict]:
        """Return the full registration record for ``name``, or None."""
        with self._lock:
            rec = self._read().get(name)
            return dict(rec) if rec else None

    def list(self) -> Dict[str, dict]:
        """Return the whole registry (name -> record)."""
        with self._lock:
            return {name: dict(rec) for name, rec in self._read().items()}


# Global registry — import this everywhere.
port_manager = PortManager()
