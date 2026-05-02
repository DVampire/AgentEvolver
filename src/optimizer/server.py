"""OptimizerManagerServer — singleton server for optimizer registration and dispatch, mirrors AgentManagerServer."""

import os
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

from src.config import config
from src.logger import logger
from src.optimizer.context import OptimizerConfig, OptimizerContextManager
from src.optimizer.types import Optimizer
from src.session import SessionContext
from src.utils import assemble_project_path


class OptimizerManagerServer(BaseModel):
    """Singleton server that wraps OptimizerContextManager, mirrors AgentManagerServer."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: Optional[str] = Field(default=None)
    save_path: Optional[str] = Field(default=None)

    def __init__(self, base_dir: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self._registered_configs: Dict[str, OptimizerConfig] = {}

    async def initialize(self, optimizer_names: Optional[List[str]] = None):
        self.base_dir = assemble_project_path(os.path.join(config.workdir, "optimizer"))
        os.makedirs(self.base_dir, exist_ok=True)
        self.save_path = os.path.join(self.base_dir, "optimizer.json")

        logger.info(
            f"| 📁 Optimizer manager server base directory: {self.base_dir} "
            f"with save path: {self.save_path}"
        )

        self.optimizer_context_manager = OptimizerContextManager(
            base_dir=self.base_dir,
            save_path=self.save_path,
            model_name="openrouter/gemini-3-flash-preview",
        )
        await self.optimizer_context_manager.initialize(optimizer_names=optimizer_names)

        for name in await self.optimizer_context_manager.list():
            cfg = await self.optimizer_context_manager.get_info(name)
            if cfg and name not in self._registered_configs:
                self._registered_configs[name] = cfg

        logger.info("| ✅ Optimizer initialization completed")

    async def register(
        self,
        optimizer_cls: Type[Optimizer],
        optimizer_config_dict: Optional[Dict[str, Any]] = None,
        override: bool = False,
        version: Optional[str] = None,
    ) -> OptimizerConfig:
        cfg = await self.optimizer_context_manager.register(
            optimizer_cls,
            optimizer_config_dict=optimizer_config_dict,
            override=override,
            version=version,
        )
        self._registered_configs[cfg.name] = cfg
        return cfg

    async def update(
        self,
        optimizer_cls: Type[Optimizer],
        optimizer_config_dict: Optional[Dict[str, Any]] = None,
        new_version: Optional[str] = None,
        code: Optional[str] = None,
    ) -> OptimizerConfig:
        cfg = await self.optimizer_context_manager.update(
            optimizer_cls,
            optimizer_config_dict=optimizer_config_dict,
            new_version=new_version,
            code=code,
        )
        self._registered_configs[cfg.name] = cfg
        return cfg

    async def get(self, optimizer_name: str) -> Optional[Optimizer]:
        return await self.optimizer_context_manager.get(optimizer_name)

    async def get_info(self, optimizer_name: str) -> Optional[OptimizerConfig]:
        return await self.optimizer_context_manager.get_info(optimizer_name)

    async def list(self) -> List[str]:
        return await self.optimizer_context_manager.list()

    async def unregister(self, optimizer_name: str) -> bool:
        success = await self.optimizer_context_manager.unregister(optimizer_name)
        if success and optimizer_name in self._registered_configs:
            del self._registered_configs[optimizer_name]
        return success

    async def cleanup(self):
        await self.optimizer_context_manager.cleanup()
        self._registered_configs.clear()

    async def __call__(
        self,
        name: str,
        task: str,
        ctx: SessionContext = None,
        workdir: Optional[str] = None,
        **kwargs,
    ) -> Any:
        return await self.optimizer_context_manager(name=name, task=task, ctx=ctx, workdir=workdir, **kwargs)


# Global optimizer manager singleton
optimizer_manager = OptimizerManagerServer()
