"""Plugin Manager Server

Server implementation for plugin management with lazy loading support.

The method set and its order are the ones `skill` and `connector` use — lifecycle,
registration, query, contract, execution — so a reader who knows one manager knows
this one. Two bands are deliberately different:

* There is no ``update`` / ``copy`` / ``restore``. A plugin wraps a vendor's API,
  and rewriting that adapter at runtime is not something the optimizer should do.
* ``get_instruction`` and ``function_callings`` default to **no** plugins rather
  than all of them. Every other capability's resident set is chosen in config and
  numbers in the tens; the plugin registry holds hundreds of tools for services
  most runs never touch, so a plugin reaches a model only when it is asked for.
"""

import os
from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.paths import P, path_manager
from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.plugins.context import PluginContextManager
from agentevolver.plugins.types import Plugin, PluginConfig, PluginContext
from agentevolver.response.types import Response
from agentevolver.utils import assemble_workspace_path


class PluginManagerServer(BaseModel):
    """Plugin manager server for registration and tool invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    base_dir: str = Field(default=None, description="The base directory to use for the plugins")

    def __init__(self, base_dir: Optional[str] = None, **kwargs):
        """Initialize the plugin manager server."""
        super().__init__(**kwargs)
        # Created lazily: config may not be loaded at import time. initialize()
        # reconfigures it with the proper base_dir.
        self.plugin_context_manager: Optional[PluginContextManager] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _ensure_context_manager(self) -> PluginContextManager:
        """Lazily create the context manager so methods work before initialize()."""
        if self.plugin_context_manager is None:
            self.plugin_context_manager = PluginContextManager()
        return self.plugin_context_manager

    async def initialize(self, plugin_names: Optional[List[str]] = None) -> None:
        """Build plugin instances from the PLUGIN registry.

        Args:
            plugin_names: Plugins to build. None builds every registered plugin.
        """
        self.base_dir = assemble_workspace_path(path_manager.under(config.log_root, P.LOG_MODULE, module="plugin"))
        logger.info(f"| 📁 Plugin manager server base directory: {self.base_dir}")

        self.plugin_context_manager = PluginContextManager(base_dir=self.base_dir)
        await self._ensure_context_manager().initialize(plugin_names=plugin_names)

    async def cleanup(self) -> None:
        """Tear down every plugin's provider resources."""
        await self._ensure_context_manager().cleanup()

    # ------------------------------------------------------------------
    # Register / Unregister
    # ------------------------------------------------------------------
    async def register(self,
                       plugin_cls: Type[Plugin],
                       plugin_config_dict: Optional[Dict[str, Any]] = None,
                       override: bool = False,
                       version: Optional[str] = None) -> PluginConfig:
        """Register a plugin class — the path an installed extension arrives by."""
        return await self._ensure_context_manager().register(
            plugin_cls, plugin_config_dict, override=override, version=version)

    async def unregister(self, name: str) -> bool:
        """Drop a plugin. True if one was registered."""
        return await self._ensure_context_manager().unregister(name)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------
    async def get(self, name: str) -> Optional[Plugin]:
        """Get a plugin instance by name (accepts a ``<plugin>.<tool>`` address)."""
        return await self._ensure_context_manager().get(name)

    async def get_info(self, name: str) -> Optional[Any]:
        """Descriptor for whatever ``name`` addresses: a plugin, or one of its tools."""
        return await self._ensure_context_manager().get_info(name)

    async def list(self) -> List[str]:
        """Registered plugin names."""
        return await self._ensure_context_manager().list()

    async def list_infos(self) -> List[PluginConfig]:
        """Every registered plugin config, with its tools.

        The batch form of ``list()`` + ``get_info()``, which is the enumeration
        idiom everywhere else. A plugin is a container, so a caller building the
        canvas palette wants every plugin's every tool — a hundred round trips to
        assemble one list is the reason this exists (``process`` has it for the
        same reason). Prefer ``list()`` + ``get_info(name)`` for a single lookup.
        """
        return await self._ensure_context_manager().list_infos()

    # ------------------------------------------------------------------
    # Context & Contract
    # ------------------------------------------------------------------
    async def get_instruction(self, allowlist: Optional[List[str]] = None,
                              types: Optional[List[str]] = None,
                              level: str = "brief") -> str:
        """The roster text for prompt injection.

        ``allowlist`` selects plugins by name. Unlike every other manager, ``None``
        means **none**: see the module docstring.
        """
        return await self._ensure_context_manager().get_instruction(allowlist=allowlist, types=types, level=level)

    async def function_callings(
        self, allowlist: Optional[List[str]] = None, types: Optional[List[str]] = None
    ) -> List[Tuple[Dict[str, Any], Tuple[Any, ...]]]:
        """Native call schemas for the selected plugins' tools, each with its route.

        ``allowlist`` names plugins, not tools: a plugin is the unit a person
        chooses, and its tools come with it. ``None`` = none, ``[]`` = none,
        ``[names]`` = those. Returns ``[(function_calling, ("plugin", plugin,
        tool)), ...]``.
        """
        return await self._ensure_context_manager().function_callings(allowlist=allowlist, types=types)

    async def get_schema(self, name: str, action: Optional[str] = None, format: str = "json"):
        """One plugin tool's call schema, as JSON or Markdown.

        ``name`` may carry the tool (``tavily.tavily_search``) or ``action`` may
        supply it separately — the same two spellings :meth:`__call__` accepts.
        """
        return await self._ensure_context_manager().get_schema(name, action=action, format=format)

    # ------------------------------------------------------------------
    # Plugin execution
    # ------------------------------------------------------------------
    async def __call__(self, name: str, action: str = "", input: Dict[str, Any] = None,
                       ctx: PluginContext = None, **kwargs) -> Response:
        """Invoke one of a plugin's tools.

        Args:
            name: Plugin name, or the ``<plugin>.<tool>`` address.
            action: The tool's short name, when ``name`` does not carry it. Named
                ``action`` for the same reason ``environment_manager`` is — it is
                the word the capability schema protocol already uses for the
                member of a container.
            input: Arguments for the tool.
            ctx: Calling context.

        Returns:
            The tool's canonical ``Response``; failures come back unsuccessful
            rather than raised.
        """
        return await self._ensure_context_manager()(name, action=action, input=input, ctx=ctx, **kwargs)


# Global plugin manager instance
plugin_manager = PluginManagerServer()

__all__ = ["PluginManagerServer", "plugin_manager"]
