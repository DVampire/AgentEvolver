"""The set of machines this environment can reach, and where that set is kept.

Two sources, deliberately. Hosts declared in a config are the ones a deployment ships
with — reviewed, in version control, the same for everyone who runs it. Hosts added from
the frontend are one person's working set, so they persist to the machine-level runtime
tree instead of editing a file under `configs/`. A config host and a runtime host with the
same name are the same machine; the runtime one wins, because it is the more recent
deliberate act.

Nothing here holds a credential. A host record names a machine and, at most, points at a
private key path that ssh already knows how to use — the same information `~/.ssh/config`
carries in plain text. A password field would have to be stored somewhere, and a store on
disk is the one place a password must never be.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from agentevolver.logger import logger
from agentevolver.paths import P, path_manager


class UnknownHostError(ValueError):
    """A host name that is not in the registry.

    A ValueError rather than a KeyError because it is the same *kind* of failure as a path
    outside the workspace: the caller named something that does not exist, and the action
    should report it the way it reports every other refusal instead of raising through.
    """


@dataclass
class RemoteHost:
    """One machine. ``name`` is the handle everything else uses to mean it."""

    name: str
    host: str
    user: str = ""
    port: int = 22
    identity_file: str = ""
    jump_host: str = ""
    #: Everything the agent does on this machine is confined below here. Per-host rather
    #: than global: a workspace is a property of the machine you are working on, and two
    #: machines rarely keep the same project in the same place.
    workspace_root: str = "~"
    connect_timeout: int = 15
    known_hosts_strict: bool = True
    #: Where this record came from — "config" or "runtime". Read-only for callers; the
    #: frontend uses it to show which hosts it may delete.
    origin: str = "config"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, origin: str = "config") -> "RemoteHost":
        raw = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        raw.setdefault("host", "")
        # A machine you never named is named after itself. Config blocks written for the
        # single-host form have no `name`, and asking every one of them to grow a label
        # they would only ever set to the hostname is churn for its own sake.
        raw.setdefault("name", str(raw.get("host") or "").strip())
        raw["origin"] = origin
        return cls(**raw)


class HostStore:
    """Config hosts plus the frontend's own, resolved into one ordered list."""

    def __init__(self, config_hosts: Optional[List[Dict[str, Any]]] = None):
        self._config: List[RemoteHost] = []
        for entry in config_hosts or []:
            host = RemoteHost.from_dict(entry, origin="config")
            if host.name and host.host:
                self._config.append(host)
        self._runtime: Dict[str, RemoteHost] = {}
        self._load()

    # ------------------------------------------------------------------ persistence
    @staticmethod
    def _path() -> str:
        return str(path_manager.get(P.SSH_HOSTS))

    def _load(self) -> None:
        path = self._path()
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:  # noqa: BLE001 — a corrupt store is not fatal
            logger.warning(f"| ⚠️ ssh host store unreadable ({exc}); starting from config only")
            return
        for entry in data.get("hosts", []):
            host = RemoteHost.from_dict(entry, origin="runtime")
            if host.name and host.host:
                self._runtime[host.name] = host

    def _save(self) -> None:
        path = self._path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"hosts": [host.to_dict() for host in self._runtime.values()]}
        # Written through a temporary file: the frontend can add a host while an agent is
        # reading the store, and a half-written JSON file reads as no hosts at all.
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temporary, path)

    # ------------------------------------------------------------------ queries
    def list(self) -> List[RemoteHost]:
        """Every known host, config order first, runtime additions after.

        A runtime host shadowing a config host keeps the config host's position, so a
        list the user has learned to read does not reorder itself when they edit an entry.
        """
        seen: Dict[str, RemoteHost] = {}
        for host in self._config:
            seen[host.name] = self._runtime.get(host.name, host)
        for name, host in self._runtime.items():
            seen.setdefault(name, host)
        return list(seen.values())

    def names(self) -> List[str]:
        return [host.name for host in self.list()]

    def get(self, name: str) -> Optional[RemoteHost]:
        if not name:
            return None
        for host in self.list():
            if host.name == name:
                return host
        return None

    def default(self) -> Optional[RemoteHost]:
        hosts = self.list()
        return hosts[0] if hosts else None

    # ------------------------------------------------------------------ mutation
    def add(self, entry: Dict[str, Any]) -> RemoteHost:
        """Add or replace a host, persisting it. Raises ``ValueError`` on a bad record."""
        host = RemoteHost.from_dict(entry, origin="runtime")
        if not host.host:
            raise ValueError("a host needs an address (hostname or ~/.ssh/config alias)")
        if not host.name:
            raise ValueError("a host needs a name")
        if "/" in host.name or host.name.startswith("."):
            # The name reaches a remote tmux session name and a local file path.
            raise ValueError(f"invalid host name {host.name!r}")
        try:
            host.port = int(host.port or 22)
        except (TypeError, ValueError):
            raise ValueError(f"invalid port {entry.get('port')!r}") from None
        self._runtime[host.name] = host
        self._save()
        return host

    def remove(self, name: str) -> bool:
        """Forget a runtime host. A config host cannot be removed from here.

        Deleting it would only last until the next restart, and a delete that silently
        undoes itself is worse than one that is refused.
        """
        if name not in self._runtime:
            return False
        del self._runtime[name]
        self._save()
        return True

    def removable(self, name: str) -> bool:
        return name in self._runtime
