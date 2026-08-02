"""Sandbox Manager Server.

``sandbox_manager`` is the singleton entry point for the sandbox subsystem.
Unlike the tool/agent/skill managers it carries no versioning/persistence
machinery — a sandbox is infrastructure, not an evolvable component. It:

  * lazily ensures the opensandbox-server daemon is running (via the handles),
  * hands out started :class:`~agentevolver.sandbox.types.Sandbox` handles by *kind*
    (``"opensandbox"``, ``"playwright"``, ``"vscode"``, ...),
  * optionally caches a handle per ``reuse_key`` (e.g. a session id) so callers
    can keep a warm container across calls instead of paying cold-start each time,
  * enforces each sandbox's own egress policy, standing up and tearing down the
    relay that implements it,
  * tears everything down on cleanup.

Egress policy is normally declared once, in the run's config file:

    sandbox_allow_hosts = ["api.example.com"]   # reachable even with network off
    sandbox_deny_hosts  = ["github.com"]        # never reachable

Those become the default for every sandbox this run acquires, so a run is configured
rather than each call site being individually remembered — an isolation mechanism that
depends on every caller wiring it up is one that will be missing somewhere. A single
acquire can still override them when one sandbox genuinely differs from the rest:

    await sandbox_manager.acquire("docker", image=..., deny_hosts=["github.com", "gitlab.com"])

Either way the manager does the enforcing and callers never build a relay themselves.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.registry import SANDBOX
from agentevolver.sandbox.netpolicy import NetworkPolicy, model_endpoint_hosts
from agentevolver.sandbox.process import shutdown_all
from agentevolver.sandbox.relay import EgressRelay, default_socket_path
from agentevolver.sandbox.types import Sandbox, SandboxConfig


class SandboxManagerServer(BaseModel):
    """Manages sandbox handles backed by opensandbox."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        # reuse_key -> live started handle
        self._handles: Dict[str, Sandbox] = {}
        # id(handle) -> the relay enforcing that sandbox's egress policy. Keyed by handle
        # rather than by reuse_key so an uncached sandbox's relay is still tracked and
        # still gets torn down.
        self._relays: Dict[int, EgressRelay] = {}
        # Run-wide egress defaults, read from the config at initialize().
        self.allow_hosts: List[str] = []
        self.deny_hosts: List[str] = []
        self._initialized = False

    async def initialize(self) -> None:
        """Import built-in sandbox backends so they register, and read the run's egress
        policy. Idempotent."""
        if self._initialized:
            return
        # Trigger @SANDBOX.register_module on the built-in handles.
        import agentevolver.sandbox.default  # noqa: F401

        self.allow_hosts = list(config.get("sandbox_allow_hosts", None) or [])
        self.deny_hosts = list(config.get("sandbox_deny_hosts", None) or [])
        # The endpoints cannot be named in a config file: they are whatever the
        # `*_API_BASE` environment variables point at, and mmengine parses configs with
        # lazy imports, so a config cannot call a function to work them out. The config
        # declares the intent and the derivation happens here.
        if config.get("sandbox_allow_model_endpoints", False):
            for host in model_endpoint_hosts():
                if host not in self.allow_hosts:
                    self.allow_hosts.append(host)

        self._initialized = True
        logger.info(f"| 🧩 Sandbox manager ready (kinds: {await self.list()})")
        if self.allow_hosts or self.deny_hosts:
            logger.info(f"| 🛡️  Sandbox egress defaults — allow={self.allow_hosts} deny={self.deny_hosts}")

    # ------------------------------------------------------------- discovery
    async def list(self) -> List[str]:
        await self._ensure_registered()
        return sorted(SANDBOX.module_dict.keys())

    async def get(self, kind: str) -> Type[Sandbox]:
        """Return the registered sandbox *class* for a kind (not a live handle)."""
        await self._ensure_registered()
        cls = SANDBOX.get(kind)
        if cls is None:
            raise ValueError(f"No registered sandbox kind {kind!r}. Available: {await self.list()}")
        return cls

    async def _ensure_registered(self) -> None:
        if not self._initialized:
            await self.initialize()

    # ------------------------------------------------------------- handles
    async def acquire(
        self,
        kind: str = "opensandbox",
        *,
        reuse_key: Optional[str] = None,
        start: bool = True,
        **overrides: Any,
    ) -> Sandbox:
        """Create (or reuse) a started sandbox handle of the given kind.

        Args:
            kind: registered sandbox kind (``opensandbox`` / ``playwright`` / ``vscode``).
            reuse_key: if given, the handle is cached and reused for this key
                (e.g. a session id) so the container stays warm across calls.
            start: whether to ``await handle.start()`` before returning.
            **overrides: fields forwarded to :class:`SandboxConfig` (image, env,
                timeout_minutes, network, allow_hosts, deny_hosts, ...). `allow_hosts`
                and `deny_hosts` default to the run's configured policy; naming either
                here overrides it for this sandbox only.

        Not called ``**config``: the module-level ``config`` is the run's global
        configuration, and a parameter of that name shadowed it inside this method.
        """
        cache_key = f"{kind}:{reuse_key}" if reuse_key else None
        if cache_key and cache_key in self._handles:
            handle = self._handles[cache_key]
            if await handle.is_alive():
                return handle
            self._handles.pop(cache_key, None)

        cls = await self.get(kind)
        sandbox_config = SandboxConfig(**self._with_egress_defaults(overrides))
        # One key identifies this sandbox everywhere: its relay socket and, for backends
        # that name real resources, its container. Derived from reuse_key so a leftover
        # from a killed run can be cleaned up by exact name next time.
        key = reuse_key or kind
        sandbox_config.sandbox_key = sandbox_config.sandbox_key or key
        relay = await self._start_relay(sandbox_config, key)

        handle = cls(sandbox_config)
        if relay is not None:
            self._relays[id(handle)] = relay
        try:
            if start:
                await handle.start()
        except Exception:
            # A relay outlives a failed start otherwise, leaving a stale socket and a
            # listener for a sandbox that never existed.
            #
            # The handle goes too, because a start can fail *after* the backend has
            # created real resources: DockerSandbox builds the container and only then
            # starts the egress forwarder inside it, so a policy whose forwarder cannot
            # run left the container running with nothing holding it. Nothing has been
            # cached at this point, so `release` would never find it either. Teardown
            # failure is logged rather than raised — the original error is the one the
            # caller needs to see.
            try:
                await handle.destroy()
            except Exception as teardown_error:  # noqa: BLE001
                logger.warning(
                    f"| ⚠️ Could not destroy {kind} sandbox after a failed start: {teardown_error}"
                )
            await self._stop_relay(handle)
            raise
        if cache_key:
            self._handles[cache_key] = handle
        return handle

    # ------------------------------------------------------------------ egress
    def _with_egress_defaults(self, overrides: Dict[str, Any]) -> Dict[str, Any]:
        """Apply the run's egress defaults to an acquire call that did not set them.

        An explicit argument always wins, for the one sandbox that genuinely differs from
        the rest of the run.
        """
        merged = dict(overrides)
        merged.setdefault("allow_hosts", list(self.allow_hosts))
        merged.setdefault("deny_hosts", list(self.deny_hosts))
        return merged

    async def _start_relay(self, sandbox_config: SandboxConfig, key: str) -> Optional[EgressRelay]:
        """Stand up an egress relay if this sandbox's policy needs one.

        Two policies need no relay: a fully open network (nothing to enforce) and a fully
        closed one (the sandbox is given no interface, so there is nothing to enforce it
        *with*). Everything in between — an allowlist on a closed network, a denylist on
        an open one — is enforced by a relay outside the sandbox, and `sandbox_config` is
        stamped with where the backend should attach it.
        """
        policy = NetworkPolicy.from_config(sandbox_config)
        if policy.is_unrestricted or policy.blocks_everything:
            return None

        relay = EgressRelay(default_socket_path(key), policy)
        await relay.start()
        sandbox_config.egress_socket = str(relay.socket_path)
        return relay

    async def _stop_relay(self, handle: Sandbox) -> None:
        relay = self._relays.pop(id(handle), None)
        if relay is not None:
            await relay.stop()

    def egress_audit(self, handle: Sandbox) -> Optional[Dict[str, Any]]:
        """What this sandbox tried to reach, and what was refused.

        Returns None when no policy was being enforced — which is not the same as "no
        attempts": an unrestricted sandbox is not watched at all, and reporting an empty
        denial list for it would read as evidence of isolation that was never applied.
        """
        relay = self._relays.get(id(handle))
        return relay.audit() if relay is not None else None

    async def release(self, kind: str = "opensandbox", *, reuse_key: Optional[str] = None) -> None:
        """Destroy a cached handle for a reuse_key."""
        cache_key = f"{kind}:{reuse_key}" if reuse_key else None
        if cache_key and cache_key in self._handles:
            handle = self._handles.pop(cache_key)
            await handle.destroy()
            await self._stop_relay(handle)

    async def cleanup(self) -> None:
        """Destroy all cached handles and stop the opensandbox-server daemon."""
        for handle in list(self._handles.values()):
            try:
                await handle.destroy()
            except Exception as e:
                logger.warning(f"| ⚠️ Error destroying sandbox handle: {e}")
        self._handles.clear()
        for relay in list(self._relays.values()):
            try:
                await relay.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"| ⚠️ Error stopping egress relay: {e}")
        self._relays.clear()
        await shutdown_all()


# Global sandbox manager instance
sandbox_manager = SandboxManagerServer()
