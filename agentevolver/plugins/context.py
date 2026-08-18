"""Global context manager for all plugins with lazy loading support.

Same shape as :class:`EnvironmentContextManager`: the :data:`PLUGIN` registry
hands over classes, ``_load_from_registry`` turns each into a
:class:`PluginConfig` (class + settings + version + source), and ``build``
instantiates it. A plugin's tools mirror an environment's actions — they come
off the class rather than off a decorator, because each is its own file under
``tools/``.

Plugins wrap third-party services, so the evolution half of the environment
manager (``update`` / ``copy`` / ``restore``) has no counterpart here: rewriting
a vendor's API adapter at runtime is not something the optimizer should do.
Registration, versioning and lifecycle are the same.
"""

import inspect
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Type

import inflection
from pydantic import BaseModel, ConfigDict, Field

from agentevolver.capability import CapabilitySchema, SchemaSource, roster, roster_card
from agentevolver.config import config
from agentevolver.dynamic import dynamic_manager
from agentevolver.logger import logger
from agentevolver.registry import PLUGIN
from agentevolver.response.types import Response, ResponseType
from agentevolver.plugins.types import Plugin, PluginConfig, PluginContext, PluginTool
from agentevolver.utils import assemble_workspace_path, gather_with_concurrency
from agentevolver.version import version_manager


#: What joins a container's name to a member's in a native function name. A dot is
#: what the canvas and workflow steps address a plugin tool by, and is not a legal
#: character in a function name, so the two spellings differ on purpose.
QUALIFIED_SEPARATOR = "__"

#: Signature parameters kept out of the schema a model is given. The plugin resolves
#: its own credential (see ``Plugin.secret``), and ``timeout`` is a deployment knob a
#: person sets on a canvas node rather than something a model should choose.
_MODEL_HIDDEN_PARAMS = frozenset({"api_key", "apikey", "token", "timeout"})


class PluginContextManager(BaseModel):
    """Global context manager for all plugins with lazy loading support."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None, description="The base directory to use for the plugins")

    def __init__(self, base_dir: Optional[str] = None, **kwargs):
        """Initialize the plugin context manager.

        Args:
            base_dir: Base directory for storing plugin data
        """
        super().__init__(**kwargs)

        if base_dir is not None:
            self.base_dir = assemble_workspace_path(base_dir)
        else:
            base_root = config.log_root if hasattr(config, "log_root") and config.get("log_root") else config.workspace_root
            self.base_dir = assemble_workspace_path(os.path.join(base_root, "plugin"))
        logger.info(f"| 📁 Plugin context manager base directory: {self.base_dir}.")

        self._plugin_configs: Dict[str, PluginConfig] = {}
        # Plugin version history, e.g. {"tavily": {"1.0.0": PluginConfig}}
        self._plugin_history_versions: Dict[str, Dict[str, PluginConfig]] = {}
        # get_instruction cache: (allowlist, types) -> text; cleared on registry change.
        self._instruction_cache: Dict[Tuple[Tuple[str, ...], Tuple[str, ...]], str] = {}

    async def initialize(self, plugin_names: Optional[List[str]] = None):
        """Initialize the plugin context manager.

        Args:
            plugin_names: Plugins to build. None builds every registered plugin.
        """
        await version_manager.initialize()

        plugin_configs: Dict[str, PluginConfig] = await self._load_from_registry()

        wanted = plugin_configs if plugin_names is None else {
            name: cfg for name, cfg in plugin_configs.items() if name in plugin_names
        }
        for plugin_config in wanted.values():
            try:
                await self.build(plugin_config)
            except Exception as e:  # noqa: BLE001 — one bad plugin must not abort startup
                logger.error(f"| ❌ Failed to build plugin {plugin_config.name}: {e}")

        tool_count = sum(len(cfg.tools) for cfg in self._plugin_configs.values())
        logger.info(f"| ✅ Plugins initialization completed "
                    f"({len(self._plugin_configs)} plugins, {tool_count} tools)")

    async def _load_from_registry(self) -> Dict[str, PluginConfig]:
        """Load plugins from the PLUGIN registry."""
        plugin_configs: Dict[str, PluginConfig] = {}

        async def register_plugin_class(plugin_cls: Type[Plugin]):
            """Turn one registered class into a PluginConfig."""
            try:
                plugin_config_key = inflection.underscore(plugin_cls.__name__)
                plugin_config_dict = config.get(plugin_config_key, {}) or {}
                plugin_enable_evolving = bool(plugin_config_dict.get("enable_evolving", False))

                fields = plugin_cls.model_fields
                plugin_name = fields["name"].default or plugin_config_key
                plugin_display_name = fields["display_name"].default or plugin_name
                plugin_description = fields["description"].default
                plugin_metadata = fields["metadata"].default

                plugin_version = await version_manager.get_version("plugin", plugin_name)
                plugin_code = dynamic_manager.get_full_module_source(plugin_cls)
                plugin_path = self._source_file(plugin_cls)
                manifest_path, manifest_body = self._manifest(plugin_path)

                plugin_config = PluginConfig(
                    name=plugin_name,
                    display_name=plugin_display_name,
                    description=plugin_description,
                    metadata=plugin_metadata,
                    version=plugin_version,
                    enable_evolving=plugin_enable_evolving,
                    cls=plugin_cls,
                    config=plugin_config_dict,
                    instance=None,
                    code=plugin_code,
                    path=plugin_path,
                    content=manifest_body,
                    manifest_path=manifest_path,
                )

                plugin_configs[plugin_name] = plugin_config

                if plugin_name not in self._plugin_history_versions:
                    self._plugin_history_versions[plugin_name] = {}
                self._plugin_history_versions[plugin_name][plugin_version] = plugin_config

                await version_manager.register_version("plugin", plugin_name, plugin_version)

                logger.info(f"| 📝 Registered plugin: {plugin_name} ({plugin_cls.__name__})")

            except Exception as e:  # noqa: BLE001
                logger.error(f"| ❌ Failed to register plugin class {plugin_cls.__name__}: {e}")
                raise

        plugin_classes = list(PLUGIN._module_dict.values())

        logger.info(f"| 🔍 Discovering {len(plugin_classes)} plugins from PLUGIN registry")

        results = await gather_with_concurrency(
            [register_plugin_class(cls) for cls in plugin_classes],
            max_concurrency=10, return_exceptions=True,
        )
        success_count = sum(1 for r in results if not isinstance(r, Exception))

        logger.info(f"| ✅ Discovered and registered {success_count}/{len(plugin_classes)} "
                    f"plugins from PLUGIN registry")

        return plugin_configs

    @staticmethod
    def _manifest(plugin_path: Optional[str]) -> Tuple[str, str]:
        """The plugin package's PLUGIN.md — its absolute path and its body.

        Read the way a connector reads CONNECTOR.md. The manifest is generated
        from the code (``scripts/gen_plugin_manifest.py``), but its ``## Credentials``
        section is prose somebody wrote about the service — how the key is supplied,
        what the provider expects — and that is the one thing about a plugin no
        schema carries.

        A plugin with no file on disk has no manifest, which is a fact about it
        rather than a failure.
        """
        if not plugin_path:
            return "", ""
        path = os.path.join(os.path.dirname(plugin_path), "PLUGIN.md")
        if not os.path.exists(path):
            return "", ""
        try:
            raw = open(path, encoding="utf-8").read()
        except Exception:  # noqa: BLE001 — an unreadable manifest is no manifest
            return path, ""
        match = re.match(r"\A---\s*\n.*?\n---\s*\n?(.*)\Z", raw, re.DOTALL)
        return path, (match.group(1) if match else raw).strip()

    @staticmethod
    def _source_file(plugin_cls: Type[Plugin]) -> Optional[str]:
        """The file a plugin class is defined in, or ``None`` if it has none.

        A class built at runtime — an evolved one, a test's — has no file, and
        that is a fact about it rather than a failure to look one up.
        """
        try:
            return inspect.getfile(plugin_cls)
        except Exception:  # noqa: BLE001 — dynamically defined classes have no file
            return None

    async def build(self, plugin_config: PluginConfig) -> PluginConfig:
        """Build a plugin instance from config.

        Args:
            plugin_config: Plugin configuration

        Returns:
            PluginConfig: Plugin configuration with instance and tools
        """
        existing = self._plugin_configs.get(plugin_config.name)
        if existing is not None and existing.instance is not None:
            return existing

        try:
            if plugin_config.cls is None:
                raise ValueError(
                    f"Cannot create plugin {plugin_config.name}: no class provided. "
                    "Class should be loaded during initialization.")

            instance: Plugin = (plugin_config.cls(**plugin_config.config)
                                if plugin_config.config else plugin_config.cls())
            if not instance.name:
                instance.name = plugin_config.name
            await instance.initialize()

            plugin_config.instance = instance
            # The class binds its tools at construction; surface them on the
            # config the way an environment surfaces its actions.
            plugin_config.tools = {tool.name: tool for tool in instance.tool_list()}
            if not plugin_config.tools:
                logger.warning(f"| ⚠️ Plugin {plugin_config.name} declares no tools")

            self._plugin_configs[plugin_config.name] = plugin_config

            logger.info(f"| ✅ Plugin {plugin_config.name} created with "
                        f"{len(plugin_config.tools)} tool(s)")
            return plugin_config
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ Failed to create plugin {plugin_config.name}: {e}")
            raise

    async def register(self,
                       plugin_cls: Type[Plugin],
                       plugin_config_dict: Optional[Dict[str, Any]] = None,
                       override: bool = False,
                       version: Optional[str] = None) -> PluginConfig:
        """Register a plugin class — the path an installed extension arrives by.

        Takes a class and its settings rather than a live instance, so it matches
        what ``tool_manager`` / ``agent_manager`` / ``environment_manager`` take:
        ``ExtensionManager`` loads a class off disk and hands it to whichever
        manager owns it, and a plugin that wanted an instance was the one module
        that path could not serve.

        Args:
            plugin_cls: The plugin class to register.
            plugin_config_dict: Settings for its constructor. ``None`` falls back
                to the global config block named after the class.
            override: Whether to replace an existing registration of the same name.
            version: Version string; ``None`` asks ``version_manager`` for one.

        Returns:
            The registered :class:`PluginConfig`, with its instance built.

        Raises:
            ValueError: If the name is empty, or already registered without
                ``override``.
        """
        if plugin_config_dict is None:
            plugin_config_key = inflection.underscore(plugin_cls.__name__)
            plugin_config_dict = config.get(plugin_config_key, {}) or {}

        instance: Plugin = plugin_cls(**plugin_config_dict) if plugin_config_dict else plugin_cls()
        plugin_name = instance.name
        if not plugin_name:
            raise ValueError("Plugin.name cannot be empty.")
        if plugin_name in self._plugin_configs and not override:
            raise ValueError(f"Plugin '{plugin_name}' already registered. Use override=True to replace it.")

        await instance.initialize()
        plugin_version = version or await version_manager.get_version("plugin", plugin_name)
        source_file = self._source_file(plugin_cls)
        manifest_path, manifest_body = self._manifest(source_file)
        plugin_config = PluginConfig(
            name=plugin_name,
            display_name=instance.display_name or plugin_name,
            description=instance.description,
            metadata=instance.metadata,
            version=plugin_version,
            enable_evolving=bool(plugin_config_dict.get("enable_evolving", False)),
            cls=plugin_cls,
            config=plugin_config_dict,
            instance=instance,
            code=dynamic_manager.get_full_module_source(plugin_cls),
            path=source_file,
            content=manifest_body,
            manifest_path=manifest_path,
            tools={tool.name: tool for tool in instance.tool_list()},
        )
        self._plugin_configs[plugin_name] = plugin_config
        self._plugin_history_versions.setdefault(plugin_name, {})[plugin_version] = plugin_config
        self._invalidate_instruction()
        await version_manager.register_version("plugin", plugin_name, plugin_version)
        logger.info(f"| 📝 Registered plugin: {plugin_name} ({plugin_cls.__name__})@{plugin_version}")
        return plugin_config

    async def unregister(self, plugin_name: str) -> bool:
        """Drop a plugin, cleaning up its instance first. True if one was there."""
        plugin_config = self._plugin_configs.pop(plugin_name, None)
        if plugin_config is None:
            return False
        if plugin_config.instance is not None:
            try:
                await plugin_config.instance.cleanup()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"| ⚠️ Error cleaning up plugin {plugin_name}: {e}")
        self._plugin_history_versions.pop(plugin_name, None)
        self._invalidate_instruction()
        return True

    # ---------------------------------------------------------------- lookup
    async def get(self, plugin_name: str) -> Optional[Plugin]:
        """Get a plugin instance by name (accepts a ``<plugin>.<tool>`` address)."""
        plugin_config, _ = self._resolve(plugin_name)
        return plugin_config.instance if plugin_config else None

    async def get_info(self, name: str) -> Optional[Any]:
        """Descriptor for whatever ``name`` addresses: a plugin, or one of its tools."""
        plugin_config, tool_name = self._resolve(name)
        if plugin_config is None:
            return None
        return plugin_config.tools.get(tool_name) if tool_name else plugin_config

    async def list(self) -> List[str]:
        """Registered plugin names."""
        return list(self._plugin_configs.keys())

    async def list_infos(self) -> List[PluginConfig]:
        """Every registered plugin config, with its tools (batch enumeration)."""
        return list(self._plugin_configs.values())

    # ------------------------------------------------------------------
    # Context & Contract
    # ------------------------------------------------------------------
    async def get_instruction(self, allowlist: Optional[List[str]] = None,
                              types: Optional[List[str]] = None,
                              level: str = "brief") -> str:
        """Assemble the plugin roster for prompt injection.

        The connector shape, because a plugin is the same thing: one outside
        service with several separately callable members. Each tool is projected
        as its own native function (``{plugin}__{tool}``) with a schema derived
        from its signature, so listing the tool names here would be the roster
        paying for what the request's own ``tools`` array already states. What it
        carries instead is where the rest is — ``brief`` names PLUGIN.md,
        ``full`` is PLUGIN.md, whose ``## Credentials`` section is the part no
        schema can state.

        ``allowlist`` names plugins. Unlike every other manager ``None`` means
        **none**: see :mod:`agentevolver.plugins.server`.

        Args:
            allowlist: Plugins to include. ``None`` and ``[]`` both mean none.
            types: Filter by the plugin's ``type`` (``data_source`` / ``model`` / …).
            level: ``brief`` for the roster, ``full`` to include PLUGIN.md.

        Returns:
            The rendered cards, joined. Cached per (allowlist, types, level).
        """
        key = (tuple(allowlist or ()), tuple(types or ()), level)
        if key in self._instruction_cache:
            return self._instruction_cache[key]
        parts: List[str] = []
        for name in (allowlist or ()):
            plugin_config = self._plugin_configs.get(name)
            if plugin_config is None:
                continue
            instance = plugin_config.instance
            if types and instance is not None and getattr(instance, "type", "") not in types:
                continue
            if not any(tool.implemented for tool in plugin_config.tools.values()):
                # Registered but nothing works yet: honest on the canvas, nothing
                # to offer a model.
                continue
            parts.append(roster_card(
                plugin_config.name, plugin_config.description or "",
                meta=f"v{plugin_config.version}",
                manifest_label="PLUGIN.md",
                manifest_path=plugin_config.manifest_path,
                notes=[(getattr(instance, "instruction", "") or "").strip()],
                document=plugin_config.content or "",
                level=level,
            ))
        text = roster(parts)
        self._instruction_cache[key] = text
        return text

    def _invalidate_instruction(self) -> None:
        """Drop cached rosters so the next get_instruction rebuilds."""
        self._instruction_cache.clear()

    async def function_callings(
        self, allowlist: Optional[List[str]] = None, types: Optional[List[str]] = None
    ) -> List[Tuple[Dict[str, Any], Tuple[Any, ...]]]:
        """Native call schemas for the selected plugins' tools, each with its route.

        Names are namespace-qualified — ``tavily__tavily_search`` — for the two
        reasons an environment's are: two services may both provide a ``search``,
        and a function name may not contain a dot, which the canvas address
        ``tavily.tavily_search`` does. That address stays the internal one.
        """
        out: List[Tuple[Dict[str, Any], Tuple[Any, ...]]] = []
        for name in (allowlist or ()):
            plugin_config = self._plugin_configs.get(name)
            if plugin_config is None:
                continue
            if types and plugin_config.instance is not None and getattr(plugin_config.instance, "type", "") not in types:
                continue
            for tool in plugin_config.tools.values():
                if not tool.implemented:
                    continue
                schema = self._tool_schema(plugin_config.name, tool, format="json")
                if schema:
                    out.append((schema, ("plugin", plugin_config.name, tool.name)))
        return out

    async def get_schema(self, name: str, action: Optional[str] = None, format: str = "json"):
        """One plugin tool's call schema, as JSON or Markdown."""
        plugin_config, addressed = self._resolve(name)
        if plugin_config is None:
            return None
        tool = plugin_config.tools.get(action or addressed)
        if tool is None:
            return None
        return self._tool_schema(plugin_config.name, tool, format=format)

    @staticmethod
    def _tool_schema(plugin_name: str, tool: PluginTool, format: str = "json"):
        """Build one tool's schema from its ``__call__`` signature.

        Inferred rather than declared, the same way a Tool's is: the signature is
        what arguments are bound against, so anything else would be a second
        answer. Credentials are dropped — the plugin resolves its own key, and a
        model shown an ``api_key`` parameter is a model invited to invent one.
        """
        parameters = dynamic_manager.remove_python_type_field(
            dynamic_manager.get_parameters(type(tool)))
        properties = {key: value for key, value in (parameters.get("properties") or {}).items()
                      if key not in _MODEL_HIDDEN_PARAMS}
        required = [key for key in (parameters.get("required") or []) if key in properties]
        return CapabilitySchema(
            name=f"{plugin_name}{QUALIFIED_SEPARATOR}{tool.name}",
            description=tool.description or tool.display_name or tool.name,
            parameters={"type": "object", "properties": properties,
                        "required": required, "additionalProperties": False},
            source=SchemaSource.INFERRED,
        ).render(format)

    # ------------------------------------------------------------------
    # Plugin execution
    # ------------------------------------------------------------------
    def _resolve(self, name: str) -> Tuple[Optional[PluginConfig], str]:
        """Split ``<plugin>.<tool>`` into its config and the tool's short name."""
        plugin_config = self._plugin_configs.get(name)
        if plugin_config is not None:
            return plugin_config, ""
        plugin_name, _, tool_name = name.partition(".")
        return self._plugin_configs.get(plugin_name), tool_name

    # -------------------------------------------------------------- dispatch
    async def __call__(self, name: str, action: str = "", input: Dict[str, Any] = None,
                       ctx: PluginContext = None, **kwargs) -> Response:
        """Call one of a plugin's tools.

        Args:
            name: Plugin name, or the ``<plugin>.<tool>`` address.
            action: The tool's short name, when ``name`` does not carry it.
            input: Input for the tool.
            ctx: Calling context.

        Returns:
            The tool's canonical Response.
        """
        plugin_config, addressed_tool = self._resolve(name)
        if plugin_config is None:
            return Response(type=ResponseType.TOOL, success=False, message=f"Unknown plugin: {name}")
        if plugin_config.instance is None:
            await self.build(plugin_config)

        logger.info(f"| ✅ Using plugin {plugin_config.name}@{plugin_config.version}")
        payload = input or {}
        target = action or addressed_tool
        # A bare plugin name goes through the plugin's own ``__call__``, so a
        # single-capability plugin can keep a natural signature.
        if target:
            return await plugin_config.instance.invoke(target, **payload)
        return await plugin_config.instance(**payload)

    async def cleanup(self):
        """Cleanup all active plugins."""
        try:
            for plugin_name, plugin_config in self._plugin_configs.items():
                if plugin_config.instance is not None:
                    try:
                        await plugin_config.instance.cleanup()
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"| ⚠️ Error cleaning up plugin {plugin_name} instance: {e}")

            self._plugin_configs.clear()
            self._plugin_history_versions.clear()
            self._invalidate_instruction()

            logger.info("| 🧹 Plugin context manager cleaned up")
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ Error during plugin context manager cleanup: {e}")


__all__ = ["PluginContextManager"]
