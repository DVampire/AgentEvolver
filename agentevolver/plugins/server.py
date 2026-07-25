"""Plugin Manager — a lightweight registry-backed manager for provider plugins.

Deliberately lean (no versioning / dynamic-code machinery): plugins are provider
adapters that are built once from the :data:`PLUGIN` registry and invoked by
name. ``__call__`` returns a :class:`Response`, which the workflow runtime's
``_normalize`` unwraps to the canonical ``{message, data, files}`` shape.
"""

from typing import Any, Dict, List, Optional

import inflection
from pydantic import BaseModel, ConfigDict

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response, ResponseType
from agentevolver.plugins.types import Plugin, PluginContext


class PluginManager(BaseModel):
    """Manages provider-plugin registration and invocation."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._plugins: Dict[str, Plugin] = {}

    async def initialize(self, plugin_names: Optional[List[str]] = None) -> None:
        """Build plugin instances from the PLUGIN registry.

        ``plugin_names`` selects a subset (None = all). Each class is
        instantiated with its per-name config block (``config.get(<snake>)``),
        given a default name from its class if unset, and ``initialize()``-d.
        """
        import agentevolver.plugins  # noqa: F401 — ensure default plugins register

        for cls in list(PLUGIN._module_dict.values()):
            try:
                name_key = inflection.underscore(cls.__name__)
                cfg = config.get(name_key, {}) or {}
                instance: Plugin = cls(**cfg) if cfg else cls()
                if not instance.name:
                    instance.name = name_key
                if plugin_names is not None and instance.name not in plugin_names:
                    continue
                await instance.initialize()
                self._plugins[instance.name] = instance
                logger.info(f"| 🔌 Registered plugin: {instance.name} ({instance.kind})")
            except Exception as e:  # noqa: BLE001 — a broken plugin must not abort startup
                logger.error(f"| ❌ Failed to initialize plugin {cls.__name__}: {e}")
        logger.info("| ✅ Plugins initialization completed")

    async def list(self) -> List[str]:
        """List registered plugin names."""
        return list(self._plugins.keys())

    async def list_infos(self) -> List[Plugin]:
        """Return every registered plugin instance (for catalog/roster building)."""
        return list(self._plugins.values())

    async def get(self, name: str) -> Optional[Plugin]:
        """Get a plugin instance by name."""
        return self._plugins.get(name)

    async def get_info(self, name: str) -> Optional[Plugin]:
        """Get a plugin's descriptor (the instance doubles as its info)."""
        return self._plugins.get(name)

    async def get_schema(self, name: str, action: Optional[str] = None, format: str = "json"):
        """Plugins accept free-form provider params; no strict call schema (yet)."""
        return None

    async def register(self, plugin: Plugin, override: bool = False) -> Plugin:
        """Register a plugin instance directly (used by tests/extensions)."""
        if plugin.name in self._plugins and not override:
            raise ValueError(f"Plugin '{plugin.name}' already registered. Use override=True.")
        await plugin.initialize()
        self._plugins[plugin.name] = plugin
        return plugin

    async def __call__(self, name: str, input: Dict[str, Any], ctx: PluginContext = None, **kwargs) -> Response:
        """Invoke a plugin by name; failures come back as an unsuccessful Response."""
        plugin = self._plugins.get(name)
        if plugin is None:
            return Response(type=ResponseType.TOOL, success=False, message=f"Unknown plugin: {name}")
        try:
            return await plugin(**(input or {}))
        except Exception as e:  # noqa: BLE001 — provider/network errors are a failed result
            logger.error(f"| ❌ Plugin {name} failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Plugin {name} failed: {e}")

    async def cleanup(self) -> None:
        """Tear down every plugin's provider resources."""
        for plugin in self._plugins.values():
            try:
                await plugin.cleanup()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"| ⚠️ Error during plugin {plugin.name} cleanup: {e}")
        self._plugins.clear()


plugin_manager = PluginManager()
