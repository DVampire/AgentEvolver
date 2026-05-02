"""OptimizerContextManager — manages optimizer lifecycle and resources with lazy loading."""
import asyncio
import os
from asyncio_atexit import register as async_atexit_register
from typing import Any, Dict, List, Type, Optional, Tuple, TYPE_CHECKING
from datetime import datetime
import inflection
import json
from pydantic import BaseModel, ConfigDict, Field

from src.logger import logger
from src.config import config
from src.utils import (
    assemble_project_path,
    gather_with_concurrency,
    file_lock
)
from src.agent.types import AgentContext
from src.optimizer.types import Optimizer
from src.session import SessionContext
from src.version import version_manager
from src.dynamic import dynamic_manager
from src.registry import OPTIMIZER


class OptimizerConfig(BaseModel):
    """Optimizer configuration for registration, mirrors AgentConfig."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="The name of the optimizer")
    description: str = Field(description="The description of the optimizer")
    version: str = Field(default="1.0.0", description="Version of the optimizer")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    require_grad: bool = Field(default=False, description="Whether the optimizer requires gradients")

    cls: Optional[Any] = None
    config: Optional[Dict[str, Any]] = Field(default_factory=dict, description="The initialization configuration of the optimizer")
    instance: Optional[Any] = None

    code: Optional[str] = Field(default=None, description="Source code for dynamically generated optimizer classes (used when cls cannot be imported from a module)")

    function_calling: Optional[Dict[str, Any]] = Field(
        default=None, description="Default function calling representation"
    )
    text: Optional[str] = Field(
        default=None, description="Default text representation of the optimizer"
    )
    args_schema: Optional[Type[BaseModel]] = Field(
        default=None, description="Default args schema (BaseModel type)"
    )

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        """Dump the model to a dictionary, recursively serializing nested Pydantic models."""

        result = {
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata,
            "version": self.version,
            "require_grad": self.require_grad,

            "cls": dynamic_manager.get_class_string(self.cls) if self.cls else None,
            "config": self.config,
            "instance": None,
            "code": self.code,

            "function_calling": self.function_calling,
            "text": self.text,
            "args_schema": dynamic_manager.serialize_args_schema(self.args_schema) if self.args_schema else None,
        }

        return result

    @classmethod
    def model_validate(cls, data: Dict[str, Any]) -> 'OptimizerConfig':
        """Validate the model from a dictionary."""
        name = data.get("name")
        description = data.get("description")
        metadata = data.get("metadata", {})
        version = data.get("version")
        require_grad = data.get("require_grad", False)

        cls_ = None
        code = data.get("code")
        if code:
            class_name = dynamic_manager.extract_class_name_from_code(code)
            if class_name:
                try:
                    cls_ = dynamic_manager.load_class(
                        code,
                        class_name=class_name,
                        base_class=Optimizer,
                        context="optimizer"
                    )
                except Exception as e:
                    cls_ = None
            else:
                cls_ = None
        else:
            cls_ = None

        config = data.get("config", {})
        instance = data.get("instance", None)

        function_calling = data.get("function_calling")
        text = data.get("text")
        args_schema = dynamic_manager.deserialize_args_schema(data.get("args_schema"))

        return cls(name=name,
            description=description,
            metadata=metadata,
            version=version,
            require_grad=require_grad,
            cls=cls_,
            config=config,
            instance=instance,
            function_calling=function_calling,
            text=text,
            args_schema=args_schema
        )

    def __str__(self) -> str:
        return (
            f"OptimizerConfig(name={self.name}, "
            f"description={self.description}, "
            f"require_grad={self.require_grad})"
        )

    def __repr__(self) -> str:
        return self.__str__()


class OptimizerContextManager(BaseModel):
    """Global context manager for all optimizers with lazy loading and version history."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None, description="The base directory to use for the optimizers")
    save_path: str = Field(default=None, description="The path to save the optimizers configuration JSON")

    def __init__(
        self,
        base_dir: Optional[str] = None,
        save_path: Optional[str] = None,
        model_name: str = "openrouter/gemini-3-flash-preview",
        **kwargs: Any,
    ):
        super().__init__(**kwargs)

        if base_dir is not None:
            self.base_dir = assemble_project_path(base_dir)
        else:
            self.base_dir = assemble_project_path(os.path.join(config.workdir, "optimizer"))
        os.makedirs(self.base_dir, exist_ok=True)
        logger.info(f"| 📁 Optimizer context manager base directory: {self.base_dir}.")

        if save_path is not None:
            self.save_path = assemble_project_path(save_path)
        else:
            self.save_path = os.path.join(self.base_dir, "optimizer.json")
        logger.info(f"| 📁 Optimizer context manager save path: {self.save_path}.")

        # Current active configs (latest version)
        self._optimizer_configs: Dict[str, OptimizerConfig] = {}
        # Version history, e.g. {"optimizer_name": {"1.0.0": OptimizerConfig, ...}}
        self._optimizer_history_versions: Dict[str, Dict[str, OptimizerConfig]] = {}

        self.model_name = model_name
        self._cleanup_registered = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self, optimizer_names: Optional[List[str]] = None) -> None:
        """Initialize the optimizer context manager and all registered optimizers."""

        dynamic_manager.register_symbol("OPTIMIZER", OPTIMIZER)
        dynamic_manager.register_symbol("Optimizer", Optimizer)
        dynamic_manager.register_symbol("OptimizerConfig", OptimizerConfig)

        def optimizer_context_provider():
            return {
                "OPTIMIZER": OPTIMIZER,
                "Optimizer": Optimizer,
                "OptimizerConfig": OptimizerConfig,
            }
        dynamic_manager.register_context_provider("optimizer", optimizer_context_provider)

        # Load from registry
        optimizer_configs: Dict[str, OptimizerConfig] = {}
        registry_configs: Dict[str, OptimizerConfig] = await self._load_from_registry()
        optimizer_configs.update(registry_configs)

        # Load from saved JSON (evolve-persisted versions)
        code_configs: Dict[str, OptimizerConfig] = await self._load_from_code()

        # Merge: only override if saved version is strictly greater
        for optimizer_name, code_config in code_configs.items():
            if optimizer_name in optimizer_configs:
                registry_config = optimizer_configs[optimizer_name]
                if version_manager.compare_versions(code_config.version, registry_config.version) > 0:
                    logger.info(
                        f"| 🔄 Overriding optimizer {optimizer_name} from registry "
                        f"(v{registry_config.version}) with saved version (v{code_config.version})"
                    )
                    optimizer_configs[optimizer_name] = code_config
                else:
                    if version_manager.compare_versions(code_config.version, registry_config.version) == 0:
                        if optimizer_name in self._optimizer_history_versions:
                            self._optimizer_history_versions[optimizer_name][registry_config.version] = registry_config
            else:
                optimizer_configs[optimizer_name] = code_config

        # Filter by names if provided
        if optimizer_names is not None:
            optimizer_configs = {n: optimizer_configs[n] for n in optimizer_names if n in optimizer_configs}

        # Build all optimizer instances concurrently
        names = list(optimizer_configs.keys())
        tasks = [self.build(optimizer_configs[n]) for n in names]
        results = await gather_with_concurrency(tasks, max_concurrency=10, return_exceptions=True)

        for optimizer_name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.error(f"| ❌ Failed to initialize optimizer {optimizer_name}: {result}")
                continue
            self._optimizer_configs[optimizer_name] = result
            logger.info(f"| 🎮 Optimizer {optimizer_name} initialized")

        await self.save_to_json()

        async_atexit_register(self.cleanup)
        self._cleanup_registered = True

        logger.info("| ✅ Optimizers initialization completed")

    async def _load_from_registry(self) -> Dict[str, OptimizerConfig]:
        """Load optimizers from OPTIMIZER registry."""

        optimizer_configs: Dict[str, OptimizerConfig] = {}

        async def register_optimizer_class(optimizer_cls: Type[Optimizer]):
            try:
                config_key = inflection.underscore(optimizer_cls.__name__)
                optimizer_config_dict = getattr(config, config_key, {}) or {}
                optimizer_require_grad = optimizer_config_dict.get("require_grad", False)

                optimizer_name = optimizer_cls.model_fields['name'].default
                optimizer_description = optimizer_cls.model_fields['description'].default

                optimizer_version = await version_manager.get_version("optimizer", optimizer_name)
                optimizer_code = dynamic_manager.get_full_module_source(optimizer_cls)

                optimizer_config = OptimizerConfig(
                    name=optimizer_name,
                    description=optimizer_description,
                    version=optimizer_version,
                    require_grad=optimizer_require_grad,
                    cls=optimizer_cls,
                    config=optimizer_config_dict,
                    instance=None,
                    code=optimizer_code,
                )

                optimizer_configs[optimizer_name] = optimizer_config

                if optimizer_name not in self._optimizer_history_versions:
                    self._optimizer_history_versions[optimizer_name] = {}
                self._optimizer_history_versions[optimizer_name][optimizer_version] = optimizer_config

                await version_manager.register_version("optimizer", optimizer_name, optimizer_version)
                logger.info(f"| 📝 Registered optimizer: {optimizer_name} ({optimizer_cls.__name__})")

            except Exception as e:
                logger.error(f"| ❌ Failed to register optimizer class {optimizer_cls.__name__}: {e}")
                raise

        import src.optimizer  # noqa: F401

        optimizer_classes = list(OPTIMIZER._module_dict.values())
        logger.info(f"| 🔍 Discovering {len(optimizer_classes)} optimizers from OPTIMIZER registry")

        tasks = [register_optimizer_class(cls) for cls in optimizer_classes]
        results = await gather_with_concurrency(tasks, max_concurrency=10, return_exceptions=True)
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        logger.info(f"| ✅ Discovered and registered {success_count}/{len(optimizer_classes)} optimizers from OPTIMIZER registry")

        return optimizer_configs

    async def _load_from_code(self) -> Dict[str, OptimizerConfig]:
        """Load optimizers from saved JSON (persisted evolved versions)."""

        optimizer_configs: Dict[str, OptimizerConfig] = {}

        if not os.path.exists(self.save_path):
            logger.info(f"| 📂 Optimizer config file not found at {self.save_path}, skipping")
            return optimizer_configs

        try:
            with open(self.save_path, "r", encoding="utf-8") as f:
                load_data = json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(f"| ⚠️ Failed to parse optimizer config JSON from {self.save_path}: {e}")
            return optimizer_configs

        optimizers_data = load_data.get("optimizers", {})

        async def load_optimizer(optimizer_name: str, optimizer_data: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, OptimizerConfig], Optional[OptimizerConfig]]]:
            try:
                current_version = optimizer_data.get("current_version", "1.0.0")
                versions = optimizer_data.get("versions", {})
                if not versions:
                    return None

                version_map: Dict[str, OptimizerConfig] = {}
                current_optimizer_config: Optional[OptimizerConfig] = None

                for _, version_data in versions.items():
                    optimizer_config = OptimizerConfig.model_validate(version_data)
                    version_map[optimizer_config.version] = optimizer_config
                    if optimizer_config.version == current_version:
                        current_optimizer_config = optimizer_config

                return optimizer_name, version_map, current_optimizer_config
            except Exception as e:
                logger.error(f"| ❌ Failed to load optimizer {optimizer_name} from JSON: {e}")
                return None

        tasks = [load_optimizer(n, d) for n, d in optimizers_data.items()]
        results = await gather_with_concurrency(tasks, max_concurrency=10, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception) or result is None:
                continue
            optimizer_name, version_map, current_optimizer_config = result
            if not version_map:
                continue
            self._optimizer_history_versions[optimizer_name] = version_map
            if current_optimizer_config is not None:
                optimizer_configs[optimizer_name] = current_optimizer_config
            else:
                optimizer_configs[optimizer_name] = list(version_map.values())[-1]
            for oc in version_map.values():
                await version_manager.register_version("optimizer", optimizer_name, oc.version)

        logger.info(f"| 📂 Loaded {len(optimizer_configs)} optimizers from {self.save_path}")
        return optimizer_configs

    async def build(self, optimizer_config: OptimizerConfig) -> OptimizerConfig:
        """Create an optimizer instance."""
        if optimizer_config.name in self._optimizer_configs:
            existing = self._optimizer_configs[optimizer_config.name]
            if existing.instance is not None:
                return existing

        if optimizer_config.cls is None:
            raise ValueError(f"Cannot create optimizer {optimizer_config.name}: no class provided.")

        optimizer_instance = optimizer_config.cls(**(optimizer_config.config or {}))
        if hasattr(optimizer_instance, "initialize"):
            await optimizer_instance.initialize()

        optimizer_config.instance = optimizer_instance
        self._optimizer_configs[optimizer_config.name] = optimizer_config
        logger.info(f"| 🔧 Optimizer {optimizer_config.name} created and stored")
        return optimizer_config

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def register(
        self,
        optimizer_cls: Type[Optimizer],
        optimizer_config_dict: Optional[Dict[str, Any]] = None,
        override: bool = False,
        version: Optional[str] = None,
    ) -> OptimizerConfig:
        try:
            if optimizer_config_dict is None:
                config_key = inflection.underscore(optimizer_cls.__name__)
                optimizer_config_dict = getattr(config, config_key, {}) or {}

            optimizer_instance = optimizer_cls(**(optimizer_config_dict or {}))
            optimizer_name = optimizer_instance.name

            if optimizer_name in self._optimizer_configs and not override:
                logger.warning(f"| Optimizer {optimizer_name} already registered. Use override=True to replace.")
                return self._optimizer_configs[optimizer_name]

            optimizer_version = version or await version_manager.get_version("optimizer", optimizer_name)
            optimizer_code = dynamic_manager.get_source_code(optimizer_cls)

            optimizer_config = OptimizerConfig(
                name=optimizer_name,
                description=optimizer_instance.description,
                version=optimizer_version,
                require_grad=optimizer_config_dict.get("require_grad", False),
                cls=optimizer_cls,
                config=optimizer_config_dict or {},
                instance=optimizer_instance,
                code=optimizer_code,
            )

            self._optimizer_configs[optimizer_name] = optimizer_config
            if optimizer_name not in self._optimizer_history_versions:
                self._optimizer_history_versions[optimizer_name] = {}
            self._optimizer_history_versions[optimizer_name][optimizer_version] = optimizer_config
            await version_manager.register_version("optimizer", optimizer_name, optimizer_version)
            await self.save_to_json()

            logger.info(f"| ✅ Registered optimizer: {optimizer_name} (v{optimizer_version})")
            return optimizer_config

        except Exception as e:
            logger.error(f"| ❌ Failed to register optimizer: {e}")
            raise

    async def update(
        self,
        optimizer_cls: Type[Optimizer],
        optimizer_config_dict: Optional[Dict[str, Any]] = None,
        new_version: Optional[str] = None,
        code: Optional[str] = None,
    ) -> OptimizerConfig:
        """Update an existing optimizer with a new class and increment version."""
        try:
            if optimizer_config_dict is None:
                config_key = inflection.underscore(optimizer_cls.__name__)
                optimizer_config_dict = getattr(config, config_key, {}) or {}

            optimizer_instance = optimizer_cls(**(optimizer_config_dict or {}))
            optimizer_name = optimizer_instance.name

            original_config = self._optimizer_configs.get(optimizer_name)
            if original_config is None:
                raise ValueError(f"Optimizer {optimizer_name} not found. Use register() to add a new optimizer.")

            if new_version is None:
                new_version = await version_manager.generate_next_version("optimizer", optimizer_name, "patch")

            optimizer_code = code or dynamic_manager.get_source_code(optimizer_cls)

            updated_config = OptimizerConfig(
                name=optimizer_name,
                description=optimizer_instance.description,
                version=new_version,
                require_grad=optimizer_config_dict.get("require_grad", False),
                cls=optimizer_cls,
                config=optimizer_config_dict or {},
                instance=optimizer_instance,
                code=optimizer_code,
            )

            self._optimizer_configs[optimizer_name] = updated_config
            if optimizer_name not in self._optimizer_history_versions:
                self._optimizer_history_versions[optimizer_name] = {}
            self._optimizer_history_versions[optimizer_name][new_version] = updated_config

            await version_manager.register_version(
                "optimizer", optimizer_name, new_version,
                description=f"Updated from {original_config.version}",
            )
            await self.save_to_json()

            logger.info(f"| 🔄 Updated optimizer {optimizer_name} from v{original_config.version} to v{new_version}")
            return updated_config

        except Exception as e:
            logger.error(f"| ❌ Failed to update optimizer: {e}")
            raise

    async def get(self, optimizer_name: str) -> Optional[Optimizer]:
        optimizer_config = self._optimizer_configs.get(optimizer_name)
        if optimizer_config is None:
            logger.warning(f"| ⚠️ Optimizer {optimizer_name} not found.")
            return None
        if optimizer_config.instance is None:
            try:
                optimizer_config.instance = optimizer_config.cls(**(optimizer_config.config or {}))
            except Exception as e:
                logger.error(f"| ❌ Failed to instantiate optimizer {optimizer_name}: {e}")
                return None
        return optimizer_config.instance

    async def get_info(self, optimizer_name: str) -> Optional[OptimizerConfig]:
        return self._optimizer_configs.get(optimizer_name)

    async def list(self) -> List[str]:
        return list(self._optimizer_configs.keys())

    async def unregister(self, optimizer_name: str) -> bool:
        if optimizer_name not in self._optimizer_configs:
            logger.warning(f"| ⚠️ Optimizer {optimizer_name} not found.")
            return False
        optimizer_config = self._optimizer_configs[optimizer_name]
        del self._optimizer_configs[optimizer_name]
        await self.save_to_json()
        logger.info(f"| 🗑️ Unregistered optimizer {optimizer_name}@{optimizer_config.version}")
        return True

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def save_to_json(self, file_path: Optional[str] = None) -> str:
        file_path = file_path or self.save_path

        async with file_lock(file_path):
            parent_dir = os.path.dirname(file_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            save_data = {
                "metadata": {
                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "num_optimizers": len(self._optimizer_configs),
                    "num_versions": sum(len(v) for v in self._optimizer_history_versions.values()),
                },
                "optimizers": {}
            }

            for optimizer_name, version_map in self._optimizer_history_versions.items():
                try:
                    versions_data: Dict[str, Dict[str, Any]] = {}
                    for _, optimizer_config in version_map.items():
                        versions_data[optimizer_config.version] = optimizer_config.model_dump()

                    current_version = None
                    if optimizer_name in self._optimizer_configs:
                        current_version = self._optimizer_configs[optimizer_name].version
                    if current_version is None and version_map:
                        latest = None
                        for v in version_map.keys():
                            if latest is None or version_manager.compare_versions(v, latest) > 0:
                                latest = v
                        current_version = latest

                    save_data["optimizers"][optimizer_name] = {
                        "versions": versions_data,
                        "current_version": current_version,
                    }
                except Exception as e:
                    logger.warning(f"| ⚠️ Failed to serialize optimizer {optimizer_name}: {e}")
                    continue

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=4, ensure_ascii=False)

            logger.info(f"| 💾 Saved {len(self._optimizer_configs)} optimizers to {file_path}")
            return str(file_path)

    # ------------------------------------------------------------------
    # Cleanup & dispatch
    # ------------------------------------------------------------------

    async def cleanup(self):
        self._optimizer_configs.clear()
        self._optimizer_history_versions.clear()
        logger.info("| 🧹 Optimizer context manager cleaned up")

    async def __call__(
        self,
        name: str,
        task: str,
        ctx: SessionContext = None,
        workdir: Optional[str] = None,
        **kwargs,
    ) -> Any:
        if ctx is None:
            ctx = SessionContext()
        agent_ctx = AgentContext.from_session(ctx, agent_name=name, workdir=workdir)

        optimizer_info = await self.get_info(name)
        if optimizer_info is None:
            raise ValueError(f"Optimizer '{name}' not found.")

        optimizer_instance = optimizer_info.instance
        if optimizer_instance is None:
            raise ValueError(f"Optimizer '{name}' has no instance.")

        logger.info(f"| ✅ Using optimizer {name}@{optimizer_info.version}")
        return await optimizer_instance(task=task, ctx=agent_ctx, **kwargs)
