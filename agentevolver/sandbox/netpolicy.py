"""Egress policy for sandboxes: which hosts a sandbox may reach, and the record of
every attempt.

A sandbox that runs untrusted work usually needs *some* network — the agent brain has
to reach a model endpoint — while the work itself must not reach the open internet. A
boolean `network` flag cannot express that, so this module carries the allow/deny lists
and the decision function, and `agentevolver.sandbox.relay` enforces them from outside
the sandbox.

Why enforcement lives outside: a policy checked inside the sandbox is a policy the
sandboxed process can edit. The container is given no network interface at all (only
loopback) and its single route out is a proxy whose decisions are made by a host-side
process it cannot reach. Denying is then the default state of the world rather than a
rule someone has to remember to apply.

Matching rules:

- `deny` beats `allow`, always. A host on both lists is denied.
- An entry is either a bare host (`api.example.com`), a wildcard
  (`*.example.com`, which matches sub-domains but not the bare domain), or
  `host:port` to pin a single port.
- Matching is case-insensitive and ignores a trailing dot.
- With `default_allow=False` (the norm for task sandboxes) anything unmatched is
  denied, so forgetting to list a host fails closed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence
from urllib.parse import urlparse

from pydantic import BaseModel, Field

#: Hosts to fall back on for a provider whose base URL is not configured explicitly.
#: Only consulted when no matching `*_API_BASE` is set, because a deployment that
#: overrides the base URL is the case that matters: the endpoint actually in use here is
#: not any of these, and an allowlist naming the public host would have let every model
#: call fail closed while looking correct.
_DEFAULT_MODEL_HOSTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "openrouter.ai",
)


def model_endpoint_hosts(env: Optional[dict] = None) -> List[str]:
    """The hosts a model call needs, read from the environment rather than hardcoded.

    Every `*_API_BASE` variable that is set contributes its hostname; if none are, the
    public defaults are used. This is what an offline sandbox's allowlist should be built
    from: the one thing the agent brain must reach is whichever endpoint this deployment
    actually routes to, and that is a deployment detail. Hardcoding a provider's public
    host produces an allowlist that looks right and blocks every model call.
    """
    env = os.environ if env is None else env
    hosts: List[str] = []
    for key, value in env.items():
        if not key.endswith("_API_BASE") or not value:
            continue
        host = urlparse(value if "://" in value else f"https://{value}").hostname
        if host and host not in hosts:
            hosts.append(host)
    return hosts or list(_DEFAULT_MODEL_HOSTS)


class NetworkDecision(BaseModel):
    """One egress attempt and what was done about it.

    These accumulate into an audit trail: a run that claims "the agent had no internet"
    can be checked against the denials that were actually recorded, rather than trusting
    that the isolation was configured correctly.
    """

    host: str
    port: int
    allowed: bool
    reason: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        verdict = "ALLOW" if self.allowed else "DENY "
        return f"{verdict} {self.host}:{self.port} ({self.reason})"


def _normalise(host: str) -> str:
    return (host or "").strip().rstrip(".").lower()


def _split_entry(entry: str) -> tuple[str, Optional[int]]:
    """Split an `allow`/`deny` entry into (host pattern, optional port).

    A bare IPv6 literal is full of colons, so a trailing `:<digits>` cannot be taken as a
    port unconditionally: `2001:db8::1` would become host `2001:db8:` on port 1, and the
    rule would then match nothing while looking perfectly reasonable in a config file. A
    port on an IPv6 address has to be written the standard way, `[2001:db8::1]:443`.
    """
    entry = _normalise(entry)

    if entry.startswith("["):
        host, sep, tail = entry.partition("]")
        host = host[1:]
        if sep and tail.startswith(":") and tail[1:].isdigit():
            return host, int(tail[1:])
        return host, None

    # Two or more colons means an IPv6 literal, which carries no port in this form.
    if entry.count(":") > 1:
        return entry, None

    host, sep, tail = entry.rpartition(":")
    if sep and tail.isdigit():
        return host, int(tail)
    return entry, None


class NetworkPolicy:
    """Decides whether a sandbox may open a connection to `host:port`."""

    def __init__(
        self,
        allow: Optional[Sequence[str]] = None,
        deny: Optional[Sequence[str]] = None,
        *,
        default_allow: bool = False,
    ) -> None:
        self.allow: List[str] = [_normalise(a) for a in (allow or []) if _normalise(a)]
        self.deny: List[str] = [_normalise(d) for d in (deny or []) if _normalise(d)]
        self.default_allow = default_allow

    @classmethod
    def from_config(cls, config: object) -> "NetworkPolicy":
        """Build a policy from a SandboxConfig-like object.

        `network` sets what happens to an unmatched host: with network off, an
        allowlist is the only way out; with it on, the lists act as exceptions to an
        otherwise-open connection.
        """
        network = bool(getattr(config, "network", True))
        return cls(
            allow=getattr(config, "allow_hosts", None),
            deny=getattr(config, "deny_hosts", None),
            default_allow=network,
        )

    @property
    def is_unrestricted(self) -> bool:
        """True when the policy would allow everything, so no relay is needed."""
        return self.default_allow and not self.deny

    @property
    def blocks_everything(self) -> bool:
        """True when nothing can get out, so the sandbox needs no network at all."""
        return not self.default_allow and not self.allow

    @staticmethod
    def _matches(host: str, port: int, entries: Iterable[str]) -> Optional[str]:
        """Return the matching entry, or None."""
        for entry in entries:
            pattern, entry_port = _split_entry(entry)
            if entry_port is not None and entry_port != port:
                continue
            if pattern.startswith("*."):
                # A wildcard covers sub-domains only: *.example.com matches
                # api.example.com but not example.com, which has to be listed if it is
                # meant to be reachable.
                if host.endswith(pattern[1:]):
                    return entry
            elif host == pattern:
                return entry
        return None

    def decide(self, host: str, port: int) -> NetworkDecision:
        host = _normalise(host)
        denied_by = self._matches(host, port, self.deny)
        if denied_by is not None:
            return NetworkDecision(
                host=host, port=port, allowed=False,
                reason=f"denied by rule {denied_by!r}",
            )
        allowed_by = self._matches(host, port, self.allow)
        if allowed_by is not None:
            return NetworkDecision(
                host=host, port=port, allowed=True,
                reason=f"allowed by rule {allowed_by!r}",
            )
        if self.default_allow:
            return NetworkDecision(
                host=host, port=port, allowed=True, reason="not denied; network is open",
            )
        return NetworkDecision(
            host=host, port=port, allowed=False,
            reason="not on the allowlist" if self.allow else "no egress permitted",
        )

    def describe(self) -> str:
        if self.is_unrestricted:
            return "network: open"
        if self.blocks_everything:
            return "network: none"
        base = "open" if self.default_allow else "closed"
        parts = [f"network: {base}"]
        if self.allow:
            parts.append(f"allow={self.allow}")
        if self.deny:
            parts.append(f"deny={self.deny}")
        return ", ".join(parts)
