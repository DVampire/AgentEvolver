"""Prompt Context Manager — md-file-based prompt lifecycle with version management."""

import asyncio
import glob
import os
from asyncio_atexit import register as async_atexit_register
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from src.agent.optimizer.types import Variable

from src.logger import logger
from src.config import config
from src.version import version_manager
from src.utils import assemble_project_path, gather_with_concurrency
from src.utils.file_utils import file_lock
from src.prompt.types import Prompt, PromptConfig, parse_prompt_file, parse_prompt_text
from src.message.types import Message


class PromptContextManager(BaseModel):
    """Global context manager for all prompts with version management."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None)
    save_path: str = Field(default=None)
    contract_path: str = Field(default=None)

    def __init__(self,
                 base_dir: Optional[str] = None,
                 save_path: Optional[str] = None,
                 contract_path: Optional[str] = None,
                 **kwargs):
        super().__init__(**kwargs)

        if base_dir is not None:
            self.base_dir = assemble_project_path(base_dir)
        else:
            self.base_dir = assemble_project_path(os.path.join(config.workdir, "prompt"))
        os.makedirs(self.base_dir, exist_ok=True)

        self.save_path = assemble_project_path(save_path) if save_path else os.path.join(self.base_dir, "prompt.json")
        self.contract_path = assemble_project_path(contract_path) if contract_path else os.path.join(self.base_dir, "contract.md")

        logger.info(f"| 📁 Prompt context manager base_dir={self.base_dir} save_path={self.save_path}")

        self._prompt_configs: Dict[str, PromptConfig] = {}
        self._prompt_history_versions: Dict[str, Dict[str, PromptConfig]] = {}

        self._cleanup_registered = False
        self._variables_lock = asyncio.Lock()

        if not self._cleanup_registered:
            async_atexit_register(self.cleanup)
            self._cleanup_registered = True

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self, prompt_names: Optional[List[str]] = None):
        """Load prompts from md directory, then overlay JSON-versioned overrides."""
        template_dir = os.path.join(os.path.dirname(__file__), "template")
        md_configs = await self._load_from_registry(template_dir)
        json_configs = await self._load_from_code()

        merged: Dict[str, PromptConfig] = dict(md_configs)
        for name, json_cfg in json_configs.items():
            if name in merged:
                if version_manager.compare_versions(json_cfg.version, merged[name].version) > 0:
                    logger.info(f"| 🔄 Override {name}: md v{merged[name].version} → json v{json_cfg.version}")
                    merged[name] = json_cfg
                else:
                    logger.info(f"| 📌 Keep md {name} v{merged[name].version} (json v{json_cfg.version} not greater)")
            else:
                merged[name] = json_cfg

        if prompt_names is not None:
            merged = {k: v for k, v in merged.items() if k in prompt_names}

        for name, cfg in merged.items():
            self._prompt_configs[name] = cfg
            if name not in self._prompt_history_versions:
                self._prompt_history_versions[name] = {}
            self._prompt_history_versions[name][cfg.version] = cfg
            await version_manager.register_version("prompt", name, cfg.version)
            logger.info(f"| 🔧 Prompt {name} v{cfg.version} ready")

        await self.save_to_json()
        await self.save_contract(prompt_names=list(merged.keys()))
        logger.info("| ✅ Prompts initialization completed")

    async def _load_from_registry(self, template_dir: str) -> Dict[str, PromptConfig]:
        """Scan template_dir for *.md files and parse each into a PromptConfig."""
        configs: Dict[str, PromptConfig] = {}
        md_files = glob.glob(os.path.join(template_dir, "*.md"))
        logger.info(f"| 🔍 Found {len(md_files)} md files in {template_dir}")

        for path in md_files:
            try:
                cfg = parse_prompt_file(path)
                if not cfg.name:
                    logger.warning(f"| ⚠️ Skipping {path}: missing 'name' in frontmatter")
                    continue
                configs[cfg.name] = cfg
                logger.info(f"| 📝 Loaded prompt '{cfg.name}' from {os.path.basename(path)}")
            except Exception as e:
                logger.error(f"| ❌ Failed to parse {path}: {e}")

        return configs

    async def _load_from_code(self) -> Dict[str, PromptConfig]:
        """Load versioned prompt configs from JSON save file."""
        configs: Dict[str, PromptConfig] = {}

        if not os.path.exists(self.save_path):
            logger.info(f"| 📂 No prompt.json at {self.save_path}, skipping")
            return configs

        try:
            with open(self.save_path, "r", encoding="utf-8") as f:
                load_data = json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(f"| ⚠️ Failed to parse prompt.json: {e}")
            return configs

        prompts_data = load_data.get("prompts", {})

        for prompt_name, prompt_data in prompts_data.items():
            try:
                current_version = prompt_data.get("current_version", "1.0.0")
                versions = prompt_data.get("versions", {})
                if not versions:
                    continue

                version_map: Dict[str, PromptConfig] = {}
                current_cfg: Optional[PromptConfig] = None

                for _, version_data in versions.items():
                    # Skip old-format entries that have cls/code fields (pre-refactor)
                    if "system_template" not in version_data:
                        logger.warning(f"| ⚠️ Skipping old-format entry for {prompt_name}")
                        break
                    cfg = PromptConfig.model_validate(version_data)
                    version_map[cfg.version] = cfg
                    if cfg.version == current_version:
                        current_cfg = cfg

                if not version_map:
                    continue

                if current_cfg is None:
                    current_cfg = list(version_map.values())[-1]

                # Merge into history
                if prompt_name not in self._prompt_history_versions:
                    self._prompt_history_versions[prompt_name] = {}
                self._prompt_history_versions[prompt_name].update(version_map)

                configs[prompt_name] = current_cfg
                for v in version_map:
                    await version_manager.register_version("prompt", prompt_name, v)

            except Exception as e:
                logger.error(f"| ❌ Failed to load prompt {prompt_name} from JSON: {e}")

        logger.info(f"| 📂 Loaded {len(configs)} prompts from {self.save_path}")
        return configs

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def register(self, prompt: Dict[str, Any], *, override: bool = False) -> PromptConfig:
        """Register a prompt from a dict."""
        cfg = PromptConfig.model_validate(prompt)
        if not cfg.name:
            raise ValueError("Prompt name cannot be empty")
        if cfg.name in self._prompt_configs and not override:
            raise ValueError(f"Prompt '{cfg.name}' already registered. Use override=True.")

        version = await version_manager.get_version("prompt", cfg.name)
        cfg.version = version

        self._prompt_configs[cfg.name] = cfg
        if cfg.name not in self._prompt_history_versions:
            self._prompt_history_versions[cfg.name] = {}
        self._prompt_history_versions[cfg.name][version] = cfg
        await version_manager.register_version("prompt", cfg.name, version)
        await self.save_to_json()
        await self.save_contract()
        return cfg

    async def update(self, prompt_name: str, prompt: Dict[str, Any],
                     new_version: Optional[str] = None,
                     description: Optional[str] = None) -> PromptConfig:
        """Update an existing prompt and create a new version."""
        original = self._prompt_configs.get(prompt_name)
        if original is None:
            raise ValueError(f"Prompt '{prompt_name}' not found. Use register() first.")

        new_cfg = PromptConfig.model_validate({**original.model_dump(), **prompt})
        new_cfg.name = prompt_name

        if new_version is None:
            new_version = await version_manager.generate_next_version("prompt", prompt_name, "patch")
        new_cfg.version = new_version

        self._prompt_configs[prompt_name] = new_cfg
        if prompt_name not in self._prompt_history_versions:
            self._prompt_history_versions[prompt_name] = {}
        self._prompt_history_versions[prompt_name][new_version] = new_cfg
        await version_manager.register_version("prompt", prompt_name, new_version,
                                               description=description or f"Updated from {original.version}")
        await self.save_to_json()
        await self.save_contract()
        logger.info(f"| 📝 Updated prompt {prompt_name} v{new_version}")
        return new_cfg

    async def unregister(self, prompt_name: str) -> bool:
        if prompt_name not in self._prompt_configs:
            logger.warning(f"| ⚠️ Prompt {prompt_name} not found")
            return False
        del self._prompt_configs[prompt_name]
        await self.save_to_json()
        await self.save_contract()
        return True

    async def restore(self, prompt_name: str, version: str, auto_initialize: bool = True) -> Optional[PromptConfig]:
        version_cfg = self._prompt_history_versions.get(prompt_name, {}).get(version)
        if version_cfg is None:
            logger.warning(f"| ⚠️ Version {version} not found for prompt {prompt_name}")
            return None
        restored = PromptConfig.model_validate(version_cfg.model_dump())
        self._prompt_configs[prompt_name] = restored
        await self.save_to_json()
        await self.save_contract()
        logger.info(f"| 🔄 Restored prompt {prompt_name} to v{version}")
        return restored

    async def copy(self, prompt_name: str, new_name: Optional[str] = None,
                   new_version: Optional[str] = None, **override_config) -> PromptConfig:
        original = self._prompt_configs.get(prompt_name)
        if original is None:
            raise ValueError(f"Prompt '{prompt_name}' not found")

        target_name = new_name or prompt_name
        if new_version is None:
            new_version = await version_manager.get_version("prompt", target_name)

        new_data = original.model_dump()
        new_data["name"] = target_name
        new_data["version"] = new_version
        new_data.update({k: v for k, v in override_config.items() if k in new_data})

        new_cfg = PromptConfig.model_validate(new_data)
        self._prompt_configs[target_name] = new_cfg
        if target_name not in self._prompt_history_versions:
            self._prompt_history_versions[target_name] = {}
        self._prompt_history_versions[target_name][new_version] = new_cfg
        await version_manager.register_version("prompt", target_name, new_version,
                                               description=f"Copied from {prompt_name}@{original.version}")
        await self.save_to_json()
        await self.save_contract()
        logger.info(f"| 📋 Copied {prompt_name}@{original.version} → {target_name}@{new_version}")
        return new_cfg

    # ------------------------------------------------------------------
    # Getters
    # ------------------------------------------------------------------

    async def get(self, name: str) -> Optional[Prompt]:
        cfg = self._prompt_configs.get(name)
        return cfg.to_prompt() if cfg else None

    async def get_info(self, name: str) -> Optional[PromptConfig]:
        return self._prompt_configs.get(name)

    async def list(self) -> List[str]:
        return list(self._prompt_configs.keys())

    # ------------------------------------------------------------------
    # Message rendering
    # ------------------------------------------------------------------

    async def get_system_message(self, prompt_name: str,
                                  modules: Dict[str, Any] = None,
                                  reload: bool = False, **kwargs):
        cfg = self._prompt_configs.get(prompt_name)
        if cfg is None:
            raise ValueError(f"Prompt '{prompt_name}' not found")
        logger.info(f"| ✅ Rendering system message for {prompt_name} v{cfg.version}")
        return await cfg.to_prompt().get_system_message(modules=modules, reload=reload)

    async def get_agent_message(self, prompt_name: str,
                                 modules: Dict[str, Any] = None,
                                 reload: bool = True, **kwargs):
        cfg = self._prompt_configs.get(prompt_name)
        if cfg is None:
            raise ValueError(f"Prompt '{prompt_name}' not found")
        logger.info(f"| ✅ Rendering user message for {prompt_name} v{cfg.version}")
        return await cfg.to_prompt().get_user_message(modules=modules, reload=reload)

    async def get_messages(self, prompt_name: str,
                            system_modules: Dict[str, Any] = None,
                            agent_modules: Dict[str, Any] = None,
                            **kwargs) -> List[Message]:
        system_msg = await self.get_system_message(prompt_name, system_modules, reload=False)
        agent_msg = await self.get_agent_message(prompt_name, agent_modules, reload=True)
        return [system_msg, agent_msg]

    # ------------------------------------------------------------------
    # Trainable variables
    # ------------------------------------------------------------------

    async def get_variables(self, prompt_name: str) -> Dict[str, "Variable"]:
        """Get the Variable for a prompt (whether trainable or not)."""
        cfg = self._prompt_configs.get(prompt_name)
        if cfg is None:
            return {}
        var = await cfg.to_prompt().get_trainable_variable()
        if var is None:
            return {}
        return {prompt_name: var}

    async def get_trainable_variables(self, prompt_name: str) -> Dict[str, "Variable"]:
        """Return {name: Variable} only if require_grad=True."""
        async with self._variables_lock:
            cfg = self._prompt_configs.get(prompt_name)
            if cfg is None or not cfg.require_grad:
                return {}
            var = await cfg.to_prompt().get_trainable_variable()
            if var is None:
                return {}
            logger.debug(f"| ✅ Trainable variable '{prompt_name}' extracted")
            return {prompt_name: var}

    async def set_variables(self, prompt_name: str,
                             variable_updates: Dict[str, str],
                             new_version: Optional[str] = None,
                             description: Optional[str] = None) -> Dict[str, PromptConfig]:
        """Update a prompt from a new md text string. Persists to JSON only (no md file write).

        variable_updates: {prompt_name: new_md_text}
        """
        async with self._variables_lock:
            cfg = self._prompt_configs.get(prompt_name)
            if cfg is None:
                raise ValueError(f"Prompt '{prompt_name}' not found")

            new_md_text = variable_updates.get(prompt_name)
            if new_md_text is None:
                raise ValueError(f"No update provided for '{prompt_name}' in variable_updates")

            new_cfg = parse_prompt_text(new_md_text)
            if new_version is None:
                new_version = await version_manager.generate_next_version("prompt", prompt_name, "patch")

            updated = PromptConfig(
                name=cfg.name,
                description=new_cfg.description,
                version=new_version,
                require_grad=new_cfg.require_grad,
                system_template=new_cfg.system_template,
                user_template=new_cfg.user_template,
                variables=cfg.variables,
                metadata=cfg.metadata,
            )
            self._prompt_configs[prompt_name] = updated
            if prompt_name not in self._prompt_history_versions:
                self._prompt_history_versions[prompt_name] = {}
            self._prompt_history_versions[prompt_name][new_version] = updated
            await version_manager.register_version("prompt", prompt_name, new_version,
                                                   description=description or f"set_variables from {cfg.version}")
            await self.save_to_json()
            logger.info(f"| ✅ set_variables: {prompt_name} → v{new_version}")
            return {prompt_name: updated}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def save_to_json(self, file_path: Optional[str] = None) -> str:
        file_path = file_path or self.save_path
        async with file_lock(file_path):
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            save_data = {
                "metadata": {
                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "num_prompts": len(self._prompt_configs),
                    "num_versions": sum(len(v) for v in self._prompt_history_versions.values()),
                },
                "prompts": {},
            }
            for prompt_name, version_map in self._prompt_history_versions.items():
                versions_data = {cfg.version: cfg.model_dump() for cfg in version_map.values()}
                current_version = self._prompt_configs.get(prompt_name)
                current_v = current_version.version if current_version else (list(version_map)[-1] if version_map else "1.0.0")
                save_data["prompts"][prompt_name] = {
                    "versions": versions_data,
                    "current_version": current_v,
                }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=4, ensure_ascii=False)
        logger.info(f"| 💾 Saved {len(self._prompt_configs)} prompts to {file_path}")
        return str(file_path)

    async def load_from_json(self, file_path: Optional[str] = None, auto_initialize: bool = True) -> bool:
        file_path = file_path or self.save_path
        async with file_lock(file_path):
            if not os.path.exists(file_path):
                return False
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    load_data = json.load(f)
                for prompt_name, prompt_data in load_data.get("prompts", {}).items():
                    versions_data = prompt_data.get("versions", {})
                    current_version = prompt_data.get("current_version")
                    if not isinstance(versions_data, dict):
                        continue
                    version_map: Dict[str, PromptConfig] = {}
                    latest_cfg = None
                    for _, cfg_data in versions_data.items():
                        if "system_template" not in cfg_data:
                            break
                        cfg = PromptConfig.model_validate(cfg_data)
                        version_map[cfg.version] = cfg
                        if cfg.version == current_version or latest_cfg is None:
                            latest_cfg = cfg
                    if not version_map or latest_cfg is None:
                        continue
                    self._prompt_history_versions[prompt_name] = version_map
                    self._prompt_configs[prompt_name] = latest_cfg
                    for v in version_map:
                        await version_manager.register_version("prompt", prompt_name, v)
                return True
            except Exception as e:
                logger.error(f"| ❌ Failed to load from {file_path}: {e}")
                return False

    async def save_contract(self, prompt_names: Optional[List[str]] = None):
        names = prompt_names if prompt_names is not None else list(self._prompt_configs.keys())
        contract = []
        for i, name in enumerate(names):
            cfg = self._prompt_configs.get(name)
            if cfg is None:
                continue
            entry = (
                f"Prompt: {cfg.name}\n"
                f"Description: {cfg.description}\n"
                f"Version: {cfg.version}\n"
                f"Require Grad: {cfg.require_grad}\n\n"
                f"[System Template]\n{cfg.system_template}\n\n"
                f"[User Template]\n{cfg.user_template}\n"
            )
            contract.append(f"{i + 1:04d}\n{entry}\n")
        with open(self.contract_path, "w", encoding="utf-8") as f:
            f.write("---\n".join(contract))
        logger.info(f"| 📝 Saved contract for {len(contract)} prompts to {self.contract_path}")

    async def load_contract(self) -> str:
        with open(self.contract_path, "r", encoding="utf-8") as f:
            return f.read()

    async def cleanup(self):
        try:
            self._prompt_configs.clear()
            self._prompt_history_versions.clear()
            logger.info("| 🧹 Prompt context manager cleaned up")
        except Exception as e:
            logger.error(f"| ❌ Error during cleanup: {e}")
