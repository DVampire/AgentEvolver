"""connector manager Server — Connector Context Protocol.

Server implementation that mirrors the skill manager (Skill Context Protocol) and
tool manager (Tool Context Protocol) patterns, providing a unified interface for
connector discovery, loading, registration, update, and execution. A connector
wraps a single MCP server.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.paths import P, path_manager
from agentevolver.logger import logger
from agentevolver.config import config
from agentevolver.connector.context import ConnectorContextManager
from agentevolver.connector.types import ConnectorConfig, ConnectorContext
from agentevolver.response.types import Response
from agentevolver.utils import assemble_workspace_path
from agentevolver.capability import CapabilitySchema, SchemaSource


class ConnectorManagerServer(BaseModel):
    """connector manager Server for managing connector registration and context generation."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None, description="Base directory for connector data")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.connector_context_manager: Optional[ConnectorContextManager] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_context_manager(self) -> ConnectorContextManager:
        """Lazily create the context manager so methods work before initialize() is called."""
        if self.connector_context_manager is None:
            self.connector_context_manager = ConnectorContextManager()
        return self.connector_context_manager

    async def initialize(self, connector_names: Optional[List[str]] = None):
        """Initialize connectors by scanning default (and extension) connector directories.

        Args:
            connector_names: If provided, only these connectors are loaded.
        """
        self.base_dir = assemble_workspace_path(path_manager.under(config.log_root, P.LOG_MODULE, module="connector"))
        logger.info(
            f"| 📁 connector manager Server base directory: {self.base_dir} "
        )

        self.connector_context_manager = ConnectorContextManager(
            base_dir=self.base_dir,
        )
        await self._ensure_context_manager().initialize(connector_names=connector_names)

        logger.info("| ✅ Connectors initialization completed")

    async def cleanup(self):
        """Release all connectors."""
        await self._ensure_context_manager().cleanup()

    # ------------------------------------------------------------------
    # Register / Update / Unregister / Copy / Restore
    # ------------------------------------------------------------------

    async def register(
        self,
        connector_dir: str,
        override: bool = False,
        version: Optional[str] = None,
        enable_evolving: Optional[bool] = None,
    ) -> ConnectorConfig:
        """Register a connector from a directory containing connector.json.

        Args:
            connector_dir: Path to the connector directory.
            override: If True, overwrite an existing connector with the same name.
            version: Explicit version string.
            enable_evolving: If not None, override the frontmatter-parsed evolvability flag.

        Returns:
            The registered ConnectorConfig.
        """
        return await self._ensure_context_manager().register(
            connector_dir=connector_dir,
            override=override,
            version=version,
            enable_evolving=enable_evolving,
        )

    async def update(
        self,
        name: str,
        connector_dir: Optional[str] = None,
        new_version: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
        connection: Optional[Dict[str, Any]] = None,
        actions: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConnectorConfig:
        """Update an existing connector and create a new version.

        Args:
            name: Connector name.
            connector_dir: If provided, re-parse this directory.
            new_version: Explicit new version string.
            description: Override description.
            content: Override CONNECTOR.md body content.
            connection: Override MCP connection config.
            actions: Override the action list.
            metadata: Override metadata dict.

        Returns:
            Updated ConnectorConfig.
        """
        return await self._ensure_context_manager().update(
            name=name,
            connector_dir=connector_dir,
            new_version=new_version,
            description=description,
            content=content,
            connection=connection,
            actions=actions,
            metadata=metadata,
        )

    async def unregister(self, name: str) -> bool:
        """Remove a connector.

        Args:
            name: Connector name.

        Returns:
            True if removed, False if not found.
        """
        return await self._ensure_context_manager().unregister(name)

    async def copy(
        self,
        name: str,
        new_name: Optional[str] = None,
        new_version: Optional[str] = None,
        new_connector_dir: Optional[str] = None,
    ) -> ConnectorConfig:
        """Copy an existing connector, optionally under a new name.

        Args:
            name: Source connector name.
            new_name: Name for the copy.
            new_version: Version for the copy.
            new_connector_dir: If provided, physically copies the connector directory.

        Returns:
            New ConnectorConfig.
        """
        return await self._ensure_context_manager().copy(
            name=name,
            new_name=new_name,
            new_version=new_version,
            new_connector_dir=new_connector_dir,
        )

    async def restore(self, name: str, version: str) -> Optional[ConnectorConfig]:
        """Restore a specific version of a connector from history.

        Args:
            name: Connector name.
            version: Version string to restore.

        Returns:
            Restored ConnectorConfig, or None if not found.
        """
        return await self._ensure_context_manager().restore(name, version)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def discover(self, name: str) -> Optional[List[str]]:
        """Connect to the connector's MCP server and refresh its action list."""
        return await self._ensure_context_manager().discover(name)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    async def get(self, connector_name: str) -> Optional[ConnectorConfig]:
        """Get a loaded connector by name."""
        return await self._ensure_context_manager().get(connector_name)

    async def get_info(self, connector_name: str) -> Optional[ConnectorConfig]:
        """Get connector configuration by name."""
        return await self._ensure_context_manager().get_info(connector_name)

    async def list(self) -> List[str]:
        """List all loaded connector names."""
        return await self._ensure_context_manager().list()

    # ------------------------------------------------------------------
    # Context & Contract
    # ------------------------------------------------------------------

    async def get_instruction(self, allowlist: Optional[List[str]] = None,
                              types: Optional[List[str]] = None,
                              level: str = "brief") -> str:
        """Assemble the connector instruction text for prompt injection.

        `allowlist` (connector names) selects which connectors to include (None = all,
        [] = none). `types` filters by connector type. Cached per (allowlist, types)
        until the registry changes.
        """
        return await self._ensure_context_manager().get_instruction(allowlist=allowlist, types=types, level=level)

    async def function_callings(
        self, allowlist: Optional[List[str]] = None, types: Optional[List[str]] = None
    ) -> List[Tuple[Dict[str, Any], Tuple[Any, ...]]]:
        """Native tool-calling schemas for the selected connectors — ONE function per MCP
        action, each paired with its dispatch route. The function name is
        ``{connector}__{action}`` (genuine composition: a connector has many actions, so
        the action must be named too — this is not a redundant type marker). Uses the
        connector's per-action schema when available, else a permissive object.

        Returns ``[(function_calling, ("connector", connector, action)), ...]``.
        """
        names = allowlist if allowlist is not None else await self.list()
        out: List[Tuple[Dict[str, Any], Tuple[Any, ...]]] = []
        for n in names:
            info = await self.get_info(n)
            if info is None:
                continue
            actions = getattr(info, "actions", None) or []
            for act in actions:
                fc = await self.get_schema(n, action=act, format="json")
                out.append((fc, ("connector", n, act)))
        return out

    async def get_schema(self, name: str, action: Optional[str] = None, format: str = "json"):
        """Return one MCP action's contract as the model is sent it.

        The arguments are the ``inputSchema`` the server declared, kept when the
        connector was last reached. Without one the action still goes out — the
        model can call it — but as a permissive object, marked ``legacy_fallback``
        so nothing downstream mistakes "we never asked" for "it takes anything".

        The description is the action's own, falling back to the connector's when
        the server has not been reached: one sentence about the service is a poor
        description of twenty different actions, but it is better than none.
        """
        info = await self.get_info(name)
        if info is None or not action or action not in (getattr(info, "actions", None) or []):
            return None
        schemas = getattr(info, "action_schemas", None) or {}
        parameters = schemas.get(action)
        source = SchemaSource.REMOTE
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "additionalProperties": True}
            source = SchemaSource.LEGACY_FALLBACK
        own = (getattr(info, "action_descriptions", None) or {}).get(action, "").strip()
        description = own or f"{getattr(info, 'description', '') or name} — action '{action}'"
        return CapabilitySchema(
            name=f"{name}__{action}",
            description=description,
            parameters=parameters,
            strict=parameters.get("additionalProperties") is False,
            source=source,
        ).render(format)

    # ------------------------------------------------------------------
    # Connector execution
    # ------------------------------------------------------------------

    async def __call__(
        self,
        name: str,
        action: str = "",
        input: Dict[str, Any] = None,
        ctx: ConnectorContext = None,
        **kwargs,
    ) -> Response:
        """Execute one of a connector's MCP actions.

        Args:
            name: Connector name.
            action: The MCP tool to call. The older ``{"action": ..., "args": ...}``
                envelope in ``input`` is still read when this is omitted.
            input: Arguments for the action.
            ctx: Connector context.
        """
        # Ensure ctx is always a ConnectorContext instance
        ctx = ConnectorContext.from_context(ctx) if ctx else ConnectorContext(name=name, input=input)

        return await self._ensure_context_manager()(
            name=name,
            action=action,
            input=input,
            ctx=ctx,
            **kwargs,
        )


# Global connector manager instance
connector_manager = ConnectorManagerServer()
