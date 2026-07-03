"""Connector Context Manager for loading, managing, and serving connectors (MCP servers)."""

import os
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from src.logger import logger
from src.config import config
from src.connector.types import ConnectorConfig, ConnectorContext
from src.response.types import Response, ResponseType
from src.session import SessionContext
from src.utils import assemble_project_path, file_lock
from src.version import version_manager
from src.permission import permission_manager, PermissionMode


class ConnectorContextManager(BaseModel):
    """Manages the lifecycle of connectors: discovery, loading, registration, update, and execution.

    A connector wraps a single MCP server: its connection config plus the actions
    (MCP tools) it exposes. Mirrors the SkillContextManager so the two subsystems
    behave identically; the only domain differences are that a connector is defined
    by a ``connector.json`` (instead of ``SKILL.md``) and executing one routes an
    ``action``/``args`` call to the MCP server (instead of returning instructions).
    """
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None, description="Base directory for connector runtime data")
    save_path: str = Field(default=None, description="Path to persist loaded connector configs")
    contract_path: str = Field(default=None, description="Path to save the connector contract")
    default_connectors_dir: str = Field(default=None, description="Directory for built-in default connectors")
    extension_connectors_dir: str = Field(default=None, description="Directory for generated/user connectors")

    _connector_configs: Dict[str, ConnectorConfig] = {}
    _connector_history_versions: Dict[str, Dict[str, ConnectorConfig]] = {}

    def __init__(
        self,
        base_dir: Optional[str] = None,
        save_path: Optional[str] = None,
        contract_path: Optional[str] = None,
        default_connectors_dir: Optional[str] = None,
        extension_connectors_dir: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        if base_dir is not None:
            self.base_dir = assemble_project_path(base_dir)
        else:
            self.base_dir = assemble_project_path(os.path.join(config.default_dir, "connector"))
        os.makedirs(self.base_dir, exist_ok=True)

        if save_path is not None:
            self.save_path = assemble_project_path(save_path)
        else:
            self.save_path = os.path.join(self.base_dir, "connector.json")

        if contract_path is not None:
            self.contract_path = assemble_project_path(contract_path)
        else:
            self.contract_path = os.path.join(self.base_dir, "contract.md")

        _src_dir = Path(__file__).resolve().parent
        # Built-in connectors live in the default/ dir; extension connectors are
        # managed externally (loaded by ExtensionManager into the active version).
        self.default_connectors_dir = default_connectors_dir or str(_src_dir / "default")
        self.extension_connectors_dir = extension_connectors_dir or assemble_project_path(os.path.join("extension", "connector"))

        self._connector_configs: Dict[str, ConnectorConfig] = {}
        self._connector_history_versions: Dict[str, Dict[str, ConnectorConfig]] = {}

        logger.info(f"| 📁 Connector context manager base directory: {self.base_dir}")

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self, connector_names: Optional[List[str]] = None):
        """Discover and load connectors from default and persisted sources.

        Loading only parses ``connector.json`` files (fast, no network). Actions
        declared statically in the JSON are used for prompt display; call
        ``discover()`` to connect to a server and refresh its live action list.

        Args:
            connector_names: If provided, only load these connectors.
        """
        discovered: Dict[str, ConnectorConfig] = {}

        # 1. Load from built-in default directory
        default_configs = await self._load_from_directory(Path(self.default_connectors_dir))
        discovered.update(default_configs)

        # 1b. Load from extension directory (generated/user connectors); extension overrides default
        Path(self.extension_connectors_dir).mkdir(parents=True, exist_ok=True)
        extension_configs = await self._load_from_directory(Path(self.extension_connectors_dir))
        discovered.update(extension_configs)

        # 2. Load previously persisted connectors from JSON (may contain user-registered connectors)
        persisted_configs = await self._load_from_json()
        for name, persisted_cfg in persisted_configs.items():
            if name in discovered:
                existing = discovered[name]
                if version_manager.compare_versions(persisted_cfg.version, existing.version) > 0:
                    logger.info(f"| 🔄 Overriding connector '{name}' from directory (v{existing.version}) with persisted (v{persisted_cfg.version})")
                    discovered[name] = persisted_cfg
            else:
                discovered[name] = persisted_cfg

        # 3. Filter by name if requested
        if connector_names is not None:
            filtered: Dict[str, ConnectorConfig] = {}
            for name in connector_names:
                if name in discovered:
                    filtered[name] = discovered[name]
                else:
                    logger.warning(f"| ⚠️ Requested connector '{name}' not found in discovered connectors")
            discovered = filtered

        # 4. Build text representations, register versions, and store
        for name, connector_config in discovered.items():
            connector_config.text = self._build_text_representation(connector_config)
            self._connector_configs[name] = connector_config

            if name not in self._connector_history_versions:
                self._connector_history_versions[name] = {}
            self._connector_history_versions[name][connector_config.version] = connector_config

            await version_manager.register_version("connector", name, connector_config.version)

            permission_manager.register(
                entity_name=name,
                mode=PermissionMode(connector_config.permission_mode),
            )
            logger.info(f"| 🎯 Connector '{name}' v{connector_config.version} loaded from {connector_config.connector_dir}")

        # 5. Persist
        await self.save_to_json()
        await self.save_contract()

        logger.info(f"| ✅ Connectors initialization completed — {len(self._connector_configs)} connector(s) loaded")

    # ------------------------------------------------------------------
    # Directory scanning & connector.json parsing
    # ------------------------------------------------------------------

    async def _load_from_directory(self, root_dir: Path) -> Dict[str, ConnectorConfig]:
        """Scan *root_dir* for sub-directories that contain a CONNECTOR.md file."""
        configs: Dict[str, ConnectorConfig] = {}

        if not root_dir.exists():
            logger.info(f"| 📂 Connector directory does not exist, skipping: {root_dir}")
            return configs

        for child in sorted(root_dir.iterdir()):
            if not child.is_dir():
                continue
            connector_md = child / "CONNECTOR.md"
            if not connector_md.exists():
                continue
            try:
                connector_config = self._parse_connector_dir(child)
                configs[connector_config.name] = connector_config
            except Exception as e:
                logger.error(f"| ❌ Failed to parse connector at {child}: {e}")

        return configs

    def _parse_connector_dir(self, connector_dir: Path) -> ConnectorConfig:
        """Parse a single connector directory (its CONNECTOR.md) into a ConnectorConfig.

        CONNECTOR.md is a YAML frontmatter block (name/description/version/type/
        connection/actions/...) followed by a markdown body (module intro + per-tool
        detailed docs).
        """
        connector_md = connector_dir / "CONNECTOR.md"
        raw = connector_md.read_text(encoding="utf-8")

        frontmatter, body = self._parse_frontmatter(raw)

        name = frontmatter.get("name", connector_dir.name)
        description = frontmatter.get("description", "")
        version = str(frontmatter.get("version", "1.0.0"))
        require_grad = str(frontmatter.get("require_grad", "false")).lower() == "true"
        type_value = frontmatter.get("type", "worker")
        permission_mode = frontmatter.get("permission_mode", "workspace_write")
        connection = frontmatter.get("connection", {}) or {}
        actions = list(frontmatter.get("actions", []) or [])
        action_schemas = frontmatter.get("action_schemas", {}) or {}

        reserved = {
            "name", "description", "version", "require_grad", "type",
            "permission_mode", "connection", "actions", "action_schemas",
        }
        metadata = {k: v for k, v in frontmatter.items() if k not in reserved}

        return ConnectorConfig(
            name=name,
            description=description,
            metadata=metadata,
            require_grad=require_grad,
            permission_mode=permission_mode,
            version=version,
            type=type_value,
            connector_dir=str(connector_dir),
            content=body.strip(),
            connection=connection,
            actions=actions,
            action_schemas=action_schemas,
        )

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
        """Split YAML frontmatter (between --- delimiters) from the markdown body.

        Uses a full YAML parser (not line-by-line) because ``connection`` is a
        nested mapping/list.
        """
        pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
        match = pattern.match(text)

        if not match:
            return {}, text

        try:
            frontmatter = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as e:
            logger.warning(f"| ⚠️ Failed to parse connector frontmatter: {e}")
            frontmatter = {}
        if not isinstance(frontmatter, dict):
            frontmatter = {}

        body = text[match.end():]
        return frontmatter, body

    async def _load_from_json(self) -> Dict[str, ConnectorConfig]:
        """Load previously persisted connector configs (with version history) from JSON."""
        configs: Dict[str, ConnectorConfig] = {}

        if not os.path.exists(self.save_path):
            return configs

        try:
            with open(self.save_path, "r", encoding="utf-8") as f:
                load_data = json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(f"| ⚠️ Failed to parse connector config JSON: {e}")
            return configs

        connectors_data = load_data.get("connectors", {})
        for connector_name, connector_data in connectors_data.items():
            try:
                current_version = connector_data.get("current_version", "1.0.0")
                versions = connector_data.get("versions", {})

                if not versions:
                    continue

                version_map: Dict[str, ConnectorConfig] = {}
                current_cfg: Optional[ConnectorConfig] = None

                for ver_str, ver_data in versions.items():
                    cfg = ConnectorConfig(**ver_data)
                    version_map[ver_str] = cfg
                    if ver_str == current_version:
                        current_cfg = cfg

                if connector_name not in self._connector_history_versions:
                    self._connector_history_versions[connector_name] = {}
                self._connector_history_versions[connector_name].update(version_map)

                if current_cfg is not None:
                    configs[connector_name] = current_cfg
                elif version_map:
                    configs[connector_name] = list(version_map.values())[-1]

                for cfg in version_map.values():
                    await version_manager.register_version("connector", connector_name, cfg.version)

            except Exception as e:
                logger.error(f"| ❌ Failed to load connector '{connector_name}' from JSON: {e}")

        logger.info(f"| 📂 Loaded {len(configs)} connector(s) from {self.save_path}")
        return configs

    # ------------------------------------------------------------------
    # Text representation (for prompt injection)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_text_representation(connector_config: ConnectorConfig) -> str:
        """Build a concise summary for prompt injection (name + description + actions)."""
        parts = [
            f"Connector: {connector_config.name}",
            f"Description: {connector_config.description}",
            f"Type: {connector_config.type}",
            f"Version: {connector_config.version}",
            f"Transport: {connector_config.connection.get('transport', 'unknown')}",
            f"CONNECTOR.md: {os.path.join(connector_config.connector_dir, 'CONNECTOR.md')}",
        ]

        if connector_config.actions:
            parts.append("Actions:")
            for a in connector_config.actions:
                parts.append(f"  - {a}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Register / Update / Unregister / Copy / Restore
    # ------------------------------------------------------------------

    async def register(
        self,
        connector_dir: str,
        override: bool = False,
        version: Optional[str] = None,
    ) -> ConnectorConfig:
        """Register a connector from a directory containing connector.json.

        Args:
            connector_dir: Path to the connector directory.
            override: If True, overwrite an existing connector with the same name.
            version: Explicit version string. If None, reads from connector.json or auto-generates.

        Returns:
            The registered ConnectorConfig.
        """
        connector_dir_path = Path(connector_dir)
        if not (connector_dir_path / "CONNECTOR.md").exists():
            raise FileNotFoundError(f"No CONNECTOR.md found in {connector_dir}")

        connector_config = self._parse_connector_dir(connector_dir_path)

        if version is not None:
            connector_config.version = version
        else:
            existing_version = await version_manager.get_version("connector", connector_config.name)
            if existing_version and connector_config.version == "1.0.0":
                connector_config.version = existing_version

        if connector_config.name in self._connector_configs and not override:
            raise ValueError(
                f"Connector '{connector_config.name}' already registered. Use override=True or update()."
            )

        connector_config.text = self._build_text_representation(connector_config)
        self._connector_configs[connector_config.name] = connector_config

        if connector_config.name not in self._connector_history_versions:
            self._connector_history_versions[connector_config.name] = {}
        self._connector_history_versions[connector_config.name][connector_config.version] = connector_config

        await version_manager.register_version("connector", connector_config.name, connector_config.version)

        await self.save_to_json()
        await self.save_contract()

        logger.info(f"| 📝 Registered connector: {connector_config.name} v{connector_config.version}")
        return connector_config

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

        You can either point to a new connector_dir (re-parse CONNECTOR.md) or
        update individual fields (description, content, connection, actions, metadata) in-place.

        Args:
            name: Name of the connector to update.
            connector_dir: If provided, re-parse this directory as the new connector source.
            new_version: Explicit new version. If None, auto-increments patch.
            description: Override description text.
            content: Override CONNECTOR.md body content.
            connection: Override MCP connection config.
            actions: Override the action list.
            metadata: Override metadata dict.

        Returns:
            Updated ConnectorConfig.
        """
        original = self._connector_configs.get(name)
        if original is None:
            raise ValueError(f"Connector '{name}' not found. Use register() first.")

        if connector_dir is not None:
            updated = self._parse_connector_dir(Path(connector_dir))
        else:
            updated = ConnectorConfig(**original.model_dump())

        if description is not None:
            updated.description = description
        if content is not None:
            updated.content = content
        if connection is not None:
            updated.connection = connection
        if actions is not None:
            updated.actions = actions
        if metadata is not None:
            updated.metadata = metadata

        if new_version is None:
            new_version = await version_manager.generate_next_version("connector", name, "patch")
        updated.version = new_version

        updated.text = self._build_text_representation(updated)
        self._connector_configs[name] = updated

        if name not in self._connector_history_versions:
            self._connector_history_versions[name] = {}
        self._connector_history_versions[name][new_version] = updated

        await version_manager.register_version(
            "connector", name, new_version,
            description=description or f"Updated from {original.version}",
        )

        await self.save_to_json()
        await self.save_contract()

        logger.info(f"| 🔄 Updated connector '{name}' from v{original.version} to v{new_version}")
        return updated

    async def unregister(self, name: str) -> bool:
        """Remove a connector from the active set.

        Args:
            name: Connector name to unregister.

        Returns:
            True if removed, False if not found.
        """
        if name not in self._connector_configs:
            logger.warning(f"| ⚠️ Connector '{name}' not found")
            return False

        version = self._connector_configs[name].version
        del self._connector_configs[name]

        await self.save_to_json()
        await self.save_contract()

        logger.info(f"| 🗑️ Unregistered connector '{name}' v{version}")
        return True

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
            new_name: Name for the copy. If None, keeps the original name.
            new_version: Version for the copy. If None, auto-generates.
            new_connector_dir: If provided, physically copies the connector directory.

        Returns:
            The new ConnectorConfig.
        """
        original = self._connector_configs.get(name)
        if original is None:
            raise ValueError(f"Connector '{name}' not found")

        if new_name is None:
            new_name = name

        copied = ConnectorConfig(**original.model_dump())
        copied.name = new_name

        if new_connector_dir is not None:
            dest = Path(new_connector_dir)
            if not dest.exists():
                shutil.copytree(original.connector_dir, str(dest))
            copied.connector_dir = str(dest)

        if new_version is None:
            if new_name == name:
                new_version = await version_manager.generate_next_version("connector", new_name, "patch")
            else:
                new_version = await version_manager.get_version("connector", new_name)
        copied.version = new_version

        copied.text = self._build_text_representation(copied)
        self._connector_configs[new_name] = copied

        if new_name not in self._connector_history_versions:
            self._connector_history_versions[new_name] = {}
        self._connector_history_versions[new_name][new_version] = copied

        await version_manager.register_version(
            "connector", new_name, new_version,
            description=f"Copied from {name}@{original.version}",
        )

        await self.save_to_json()
        await self.save_contract()

        logger.info(f"| 📋 Copied connector '{name}' v{original.version} -> '{new_name}' v{new_version}")
        return copied

    async def restore(self, name: str, version: str) -> Optional[ConnectorConfig]:
        """Restore a specific version of a connector from history.

        Args:
            name: Connector name.
            version: Version string to restore.

        Returns:
            Restored ConnectorConfig, or None if version not found.
        """
        version_map = self._connector_history_versions.get(name, {})
        target = version_map.get(version)
        if target is None:
            logger.warning(f"| ⚠️ Version {version} not found for connector '{name}'")
            return None

        restored = ConnectorConfig(**target.model_dump())
        restored.text = self._build_text_representation(restored)
        self._connector_configs[name] = restored

        version_history = await version_manager.get_version_history("connector", name)
        if version_history:
            if version not in version_history.versions:
                await version_manager.register_version("connector", name, version)
            version_history.current_version = version
        else:
            await version_manager.register_version("connector", name, version)

        await self.save_to_json()

        logger.info(f"| 🔄 Restored connector '{name}' to v{version}")
        return restored

    # ------------------------------------------------------------------
    # Discovery (connect to the live MCP server and refresh actions)
    # ------------------------------------------------------------------

    async def discover(self, name: str) -> Optional[List[str]]:
        """Connect to a connector's MCP server and refresh its action list.

        Unlike ``initialize`` (which only reads connector.json), this actually
        opens a session to the server. Returns the discovered action names, or
        None if the connector is unknown or the connection fails.
        """
        cfg = self._connector_configs.get(name)
        if cfg is None:
            logger.warning(f"| ⚠️ Connector '{name}' not found")
            return None

        try:
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
            except Exception as e:
                logger.error(f"| ❌ Missing MCP dependency: {e}. Install `langchain_mcp_adapters`.")
                return None

            client = MultiServerMCPClient({cfg.name: cfg.connection}, tool_name_prefix=False)
            tools = await client.get_tools(server_name=cfg.name)
            action_names = [getattr(t, "name", None) or "" for t in tools]
            action_names = [a for a in action_names if a]

            cfg.actions = action_names
            cfg.text = self._build_text_representation(cfg)

            await self.save_to_json()
            await self.save_contract()

            logger.info(f"| 🔎 Connector '{name}' discovered {len(action_names)} action(s)")
            return action_names
        except Exception as e:
            logger.error(f"| ❌ Connector '{name}' discovery failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    async def get(self, connector_name: str) -> Optional[ConnectorConfig]:
        """Get a loaded connector config by name."""
        return self._connector_configs.get(connector_name)

    async def get_info(self, connector_name: str) -> Optional[ConnectorConfig]:
        """Alias for get()."""
        return self._connector_configs.get(connector_name)

    async def list(self) -> List[str]:
        """Return names of all loaded connectors."""
        return list(self._connector_configs.keys())

    # ------------------------------------------------------------------
    # Context generation (for agent prompt)
    # ------------------------------------------------------------------

    async def get_context(self, connector_names: Optional[List[str]] = None, connector_types: Optional[List[str]] = None) -> str:
        """Build the full connector context string for prompt injection.

        Args:
            connector_names: if given, only these connectors (by name).
            connector_types: if given, only connectors whose ``type`` is in this list.
        """
        if not self._connector_configs:
            return ""

        targets = connector_names if connector_names else list(self._connector_configs.keys())
        parts: List[str] = []

        for name in targets:
            cfg = self._connector_configs.get(name)
            if cfg is None:
                continue
            if connector_types and cfg.type not in connector_types:
                continue
            parts.append(f"<connector name=\"{cfg.name}\">\n{cfg.text}\n</connector>")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Contract (persistent text summary)
    # ------------------------------------------------------------------

    async def save_contract(self, connector_names: Optional[List[str]] = None):
        """Save a human-readable contract file listing all loaded connectors."""
        targets = connector_names if connector_names else list(self._connector_configs.keys())
        lines: List[str] = []
        for idx, name in enumerate(targets):
            cfg = self._connector_configs.get(name)
            if cfg is None:
                continue
            lines.append(f"{idx + 1:04d}\n{cfg.text}\n")

        contract_text = "---\n".join(lines)
        os.makedirs(os.path.dirname(self.contract_path), exist_ok=True)
        with open(self.contract_path, "w", encoding="utf-8") as f:
            f.write(contract_text)
        logger.info(f"| 📝 Saved {len(lines)} connector(s) contract to {self.contract_path}")

    async def load_contract(self) -> str:
        """Load the contract text from disk."""
        if not os.path.exists(self.contract_path):
            return ""
        with open(self.contract_path, "r", encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------------
    # Persistence (JSON) — with version history
    # ------------------------------------------------------------------

    async def save_to_json(self, file_path: Optional[str] = None) -> str:
        """Persist all loaded connector configs with version history to JSON."""
        file_path = file_path or self.save_path

        async with file_lock(file_path):
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            save_data: Dict[str, Any] = {
                "metadata": {
                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "num_connectors": len(self._connector_configs),
                    "num_versions": sum(len(v) for v in self._connector_history_versions.values()),
                },
                "connectors": {},
            }

            for connector_name, version_map in self._connector_history_versions.items():
                versions_data: Dict[str, Any] = {}
                for ver, cfg in version_map.items():
                    versions_data[ver] = cfg.model_dump()

                current_version = None
                if connector_name in self._connector_configs:
                    current_version = self._connector_configs[connector_name].version
                if current_version is None and version_map:
                    latest = None
                    for v in version_map:
                        if latest is None or version_manager.compare_versions(v, latest) > 0:
                            latest = v
                    current_version = latest

                save_data["connectors"][connector_name] = {
                    "current_version": current_version,
                    "versions": versions_data,
                }

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=4, ensure_ascii=False)

            logger.info(f"| 💾 Saved {len(self._connector_configs)} connector(s) with version history to {file_path}")
            return file_path

    async def load_from_json(self, file_path: Optional[str] = None) -> bool:
        """Load connector configs with version history from JSON."""
        file_path = file_path or self.save_path

        async with file_lock(file_path):
            if not os.path.exists(file_path):
                logger.warning(f"| ⚠️ Connector file not found: {file_path}")
                return False

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    load_data = json.load(f)

                connectors_data = load_data.get("connectors", {})
                loaded = 0

                for connector_name, connector_data in connectors_data.items():
                    try:
                        versions = connector_data.get("versions", {})
                        if not isinstance(versions, dict):
                            continue

                        current_version_str = connector_data.get("current_version")

                        version_configs: Dict[str, ConnectorConfig] = {}
                        latest_cfg: Optional[ConnectorConfig] = None

                        for ver_str, ver_data in versions.items():
                            cfg = ConnectorConfig(**ver_data)
                            version_configs[ver_str] = cfg

                            if current_version_str and cfg.version == current_version_str:
                                latest_cfg = cfg
                            elif latest_cfg is None:
                                latest_cfg = cfg

                        self._connector_history_versions[connector_name] = version_configs

                        if latest_cfg:
                            self._connector_configs[connector_name] = latest_cfg
                            for cfg in version_configs.values():
                                await version_manager.register_version("connector", connector_name, cfg.version)
                            loaded += 1

                    except Exception as e:
                        logger.error(f"| ❌ Failed to load connector '{connector_name}': {e}")

                logger.info(f"| 📂 Loaded {loaded} connector(s) with version history from {file_path}")
                return True

            except Exception as e:
                logger.error(f"| ❌ Failed to load connectors from {file_path}: {e}")
                return False

    # ------------------------------------------------------------------
    # Connector execution (__call__) — route an action/args call to the MCP server
    # ------------------------------------------------------------------

    async def __call__(
        self,
        name: str,
        input: Dict[str, Any],
        ctx: SessionContext = None,
        **kwargs,
    ) -> Response:
        """Execute a connector by routing an ``action``/``args`` call to its MCP server.

        Args:
            name: Connector (MCP server) name.
            input: {"action": <mcp tool name>, "args": <dict payload>}.
            ctx: Connector context.
        """
        connector_config = self._connector_configs.get(name)
        if connector_config is None:
            return Response(
                type=ResponseType.CONNECTOR,
                success=False,
                message=f"Connector '{name}' not found. Available connectors: {list(self._connector_configs.keys())}",
            )

        action = input.get("action")
        args = input.get("args") or {}
        if not action:
            return Response(
                type=ResponseType.CONNECTOR,
                success=False,
                message="Missing 'action' in input. Expected {'action': <mcp tool name>, 'args': {...}}.",
            )

        logger.info(f"| 🎯 Executing connector '{name}' action '{action}' with args: {args}")
        return await self._invoke_mcp(connector_config, action, args)

    async def _invoke_mcp(self, connector_config: ConnectorConfig, action: str, args: Dict[str, Any]) -> Response:
        """Open a session to the MCP server and invoke a single action."""
        try:
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient
                from langchain_mcp_adapters.tools import load_mcp_tools
            except Exception as e:
                return Response(
                    type=ResponseType.CONNECTOR,
                    success=False,
                    message=f"Missing MCP dependency: {e}. Install `langchain_mcp_adapters` to use connectors.",
                )

            client = MultiServerMCPClient(
                {connector_config.name: connector_config.connection},
                tool_name_prefix=False,
            )

            async with client.session(connector_config.name) as session:
                tools = await load_mcp_tools(
                    session,
                    server_name=connector_config.name,
                    tool_name_prefix=False,
                )
                tool = next((t for t in tools if getattr(t, "name", None) == action), None)
                if tool is None:
                    available = [getattr(t, "name", None) for t in tools]
                    return Response(
                        type=ResponseType.CONNECTOR,
                        success=False,
                        message=f"MCP action not found: {action} (connector={connector_config.name}). Available: {available}",
                    )

                payload = args or {}
                result = await tool.ainvoke(payload)
                msg = self._unwrap_mcp_result(result)

                return Response(
                    type=ResponseType.CONNECTOR,
                    success=True,
                    message=msg,
                    data={"connector": connector_config.name, "action": action, "args": payload},
                )
        except Exception as e:
            logger.error(f"| ❌ Connector call failed: {e}")
            return Response(type=ResponseType.CONNECTOR, success=False, message=f"Connector call failed: {e}")

    @staticmethod
    def _unwrap_mcp_result(result: Any) -> str:
        """Flatten an MCP tool result into a plain string for the agent.

        MCP tools return content blocks like ``[{"type": "text", "text": "..."}]``
        (the inner text is usually JSON). Unwrap that outer envelope so the agent
        sees the payload directly; fall back to a JSON dump / str otherwise.
        """
        def _text_of(block: Any) -> Optional[str]:
            if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
                return block["text"]
            return None

        if isinstance(result, list):
            texts = [_text_of(b) for b in result]
            if texts and all(t is not None for t in texts):
                return "\n".join(texts)
        else:
            single = _text_of(result)
            if single is not None:
                return single

        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self):
        """Release all loaded connectors."""
        self._connector_configs.clear()
        self._connector_history_versions.clear()
        logger.info("| 🧹 Connector context manager cleaned up")
