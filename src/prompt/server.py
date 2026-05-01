"""Prompt Manager

Thin wrapper over PromptContextManager with version management.
"""

import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.config import config
from src.utils import assemble_project_path
from src.logger import logger
from src.prompt.types import PromptConfig, Prompt
from src.prompt.context import PromptContextManager
from src.message.types import Message
from src.optimizer.types import Variable


class PromptManagerServer(BaseModel):
    """Prompt Manager for managing prompt registration and lifecycle."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None)
    save_path: str = Field(default=None)
    contract_path: str = Field(default=None)

    def __init__(self, base_dir: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._registered_configs: Dict[str, PromptConfig] = {}

    async def initialize(self, prompt_names: Optional[List[str]] = None):
        """Initialize prompts from md directory.

        Args:
            prompt_names: List of prompt names (md frontmatter `name:`) to load.
                          If None, all md files are loaded.
        """
        self.base_dir = assemble_project_path(os.path.join(config.workdir, "prompt"))
        os.makedirs(self.base_dir, exist_ok=True)
        self.save_path = os.path.join(self.base_dir, "prompt.json")
        self.contract_path = os.path.join(self.base_dir, "contract.md")
        logger.info(f"| 📁 Prompt Manager base_dir={self.base_dir}")

        self.prompt_context_manager = PromptContextManager(
            base_dir=self.base_dir,
            save_path=self.save_path,
            contract_path=self.contract_path,
        )
        await self.prompt_context_manager.initialize(prompt_names=prompt_names)
        logger.info("| ✅ Prompts initialization completed")

    async def register(self, prompt: Dict[str, Any], *, override: bool = False) -> PromptConfig:
        cfg = await self.prompt_context_manager.register(prompt, override=override)
        self._registered_configs[cfg.name] = cfg
        return cfg

    async def list(self) -> List[str]:
        return await self.prompt_context_manager.list()

    async def get(self, prompt_name: str) -> Optional[Prompt]:
        return await self.prompt_context_manager.get(prompt_name)

    async def get_info(self, prompt_name: str) -> Optional[PromptConfig]:
        return await self.prompt_context_manager.get_info(prompt_name)

    async def cleanup(self):
        if hasattr(self, "prompt_context_manager"):
            await self.prompt_context_manager.cleanup()
        self._registered_configs.clear()

    async def update(self, prompt_name: str, prompt: Dict[str, Any],
                     new_version: Optional[str] = None, description: Optional[str] = None) -> PromptConfig:
        cfg = await self.prompt_context_manager.update(prompt_name, prompt,
                                                        new_version=new_version,
                                                        description=description)
        self._registered_configs[cfg.name] = cfg
        return cfg

    async def copy(self, prompt_name: str, new_name: Optional[str] = None,
                   new_version: Optional[str] = None, **override_config) -> PromptConfig:
        cfg = await self.prompt_context_manager.copy(prompt_name, new_name, new_version, **override_config)
        self._registered_configs[cfg.name] = cfg
        return cfg

    async def unregister(self, prompt_name: str) -> bool:
        success = await self.prompt_context_manager.unregister(prompt_name)
        if success:
            self._registered_configs.pop(prompt_name, None)
        return success

    async def restore(self, prompt_name: str, version: str, auto_initialize: bool = True) -> Optional[PromptConfig]:
        cfg = await self.prompt_context_manager.restore(prompt_name, version, auto_initialize)
        if cfg:
            self._registered_configs[cfg.name] = cfg
        return cfg

    async def get_system_message(self, prompt_name: str,
                                  modules: Dict[str, Any] = None,
                                  reload: bool = False, **kwargs):
        if not hasattr(self, "prompt_context_manager"):
            await self.initialize()
        return await self.prompt_context_manager.get_system_message(
            prompt_name=prompt_name, modules=modules, reload=reload, **kwargs)

    async def get_agent_message(self, prompt_name: str,
                                 modules: Dict[str, Any] = None,
                                 reload: bool = True, **kwargs):
        if not hasattr(self, "prompt_context_manager"):
            await self.initialize()
        return await self.prompt_context_manager.get_agent_message(
            prompt_name=prompt_name, modules=modules, reload=reload, **kwargs)

    async def get_messages(self, prompt_name: str,
                            system_modules: Dict[str, Any] = None,
                            agent_modules: Dict[str, Any] = None,
                            **kwargs) -> List[Message]:
        return await self.prompt_context_manager.get_messages(
            prompt_name=prompt_name,
            system_modules=system_modules,
            agent_modules=agent_modules,
            **kwargs)

    async def get_variables(self, prompt_name: str) -> Dict[str, Variable]:
        return await self.prompt_context_manager.get_variables(prompt_name=prompt_name)

    async def get_trainable_variables(self, prompt_name: str) -> Dict[str, Variable]:
        return await self.prompt_context_manager.get_trainable_variables(prompt_name=prompt_name)

    async def set_variables(self, prompt_name: str,
                             variable_updates: Dict[str, str],
                             new_version: Optional[str] = None,
                             description: Optional[str] = None) -> Dict[str, PromptConfig]:
        """Update a prompt from a new md text string. variable_updates = {prompt_name: new_md_text}."""
        updated = await self.prompt_context_manager.set_variables(
            prompt_name=prompt_name,
            variable_updates=variable_updates,
            new_version=new_version,
            description=description,
        )
        for name, cfg in updated.items():
            self._registered_configs[name] = cfg
        return updated

    async def set_contract(self, prompt_names: Optional[List[str]] = None):
        await self.prompt_context_manager.save_contract(prompt_names=prompt_names)

    async def get_contract(self) -> str:
        return await self.prompt_context_manager.load_contract()


# Global Prompt Manager instance
prompt_manager = PromptManagerServer()
