"""Central port registry.

One place that (a) names the framework's well-known default ports and (b) hands
out and records host ports, so dynamic bindings (deploy sites, the Gateway, the
Trace UI) are de-conflicted and discoverable instead of being ad-hoc literals
scattered across the codebase.

Allocations are persisted to ``<home>/ports.json`` (the ``.agentevolver`` home),
so every process and every run sees the same picture of what is bound where.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

from agentevolver.logger import logger
from agentevolver.utils.path_utils import home_dir

# --- Well-known default ports (single source of truth) ----------------------
# Host-bound framework services:
GATEWAY = 9876          # Gateway WebSocket server
OPENSANDBOX = 8080      # local opensandbox-server daemon
TRACE_UI = 8765         # Trace UI web server (AGENTEVOLVER_TRACE_PORT overrides)
# Container-internal ports (fixed inside a sandbox; the opensandbox proxy maps
# them to ephemeral host ports, so these never collide on the host):
CHROME_CDP = 9222
VNC = 5900
NOVNC = 6080


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
        self._lock = threading.RLock()
        self._path: Optional[str] = None
        self._registry: Dict[str, dict] = {}

    def _ensure_loaded(self) -> None:
        if self._path is not None:
            return
        self._path = os.path.join(str(home_dir()), "ports.json")
        try:
            if os.path.exists(self._path):
                with open(self._path, encoding="utf-8") as f:
                    self._registry = json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️ Could not load port registry: {e}")
            self._registry = {}

    def _save(self) -> None:
        if not self._path:
            return
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._registry, f, indent=2)
            os.replace(tmp, self._path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"| ⚠️ Could not save port registry: {e}")

    def reserve(self, name: str, preferred: Optional[int] = None) -> int:
        """Return the host port for ``name``, allocating and recording it if new.

        Idempotent: the same name keeps its recorded port across calls and runs.
        A new name takes ``preferred`` when that port is free, otherwise an
        OS-assigned free port.
        """
        with self._lock:
            self._ensure_loaded()
            existing = self._registry.get(name)
            if existing and isinstance(existing.get("port"), int):
                return existing["port"]
            port = preferred if (preferred and is_free(preferred)) else os_free_port()
            self._registry[name] = {
                "port": port,
                "preferred": preferred,
                "pid": os.getpid(),
                "updated_at": _now(),
            }
            self._save()
            logger.info(f"| 🔌 Port reserved: {name} -> {port}")
            return port

    def record(self, name: str, port: int) -> int:
        """Record an already-decided binding (e.g. a user-specified Gateway port).

        Unlike :meth:`reserve`, this never reallocates — the caller has already
        committed to ``port``; this only makes it visible in the registry.
        """
        with self._lock:
            self._ensure_loaded()
            self._registry[name] = {
                "port": port,
                "preferred": port,
                "pid": os.getpid(),
                "updated_at": _now(),
            }
            self._save()
            return port

    def release(self, name: str) -> None:
        """Drop ``name``'s reservation (its port becomes reusable)."""
        with self._lock:
            self._ensure_loaded()
            if self._registry.pop(name, None) is not None:
                self._save()

    def get(self, name: str) -> Optional[int]:
        with self._lock:
            self._ensure_loaded()
            rec = self._registry.get(name)
            return rec.get("port") if rec else None

    def registry(self) -> Dict[str, dict]:
        """A copy of the full allocation table (for visibility)."""
        with self._lock:
            self._ensure_loaded()
            return dict(self._registry)


# Global registry — import this everywhere.
port_manager = PortManager()
