"""Constraint Context Manager for managing constraint lifecycle and resources with lazy loading."""
import os
import inspect
import asyncio
from asyncio_atexit import register as async_atexit_register
from typing import Any, Dict, List, Type, Optional, Tuple
from datetime import datetime
import inflection
import json
from pydantic import BaseModel, ConfigDict, Field


from src.logger import logger
from src.config import config
from src.utils import (assemble_project_path,
                       gather_with_concurrency,
                       file_lock
                       )
from src.constraint.types import Constraint, ConstraintConfig, ConstraintContext
from src.response.types import Response, ResponseType
from src.version import version_manager
from src.dynamic import dynamic_manager
from src.registry import CONSTRAINT


class ConstraintContextManager(BaseModel):
    """Global context manager for all constraints with lazy loading support."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None, description="The base directory to use for the constraints")
    save_path: str = Field(default=None, description="The path to save the constraints")
    contract_path: str = Field(default=None, description="The path to save the constraint contract")

    def __init__(self,
                 base_dir: Optional[str] = None,
                 save_path: Optional[str] = None,
                 contract_path: Optional[str] = None,
                 model_name: str = "openrouter/gemini-3-flash-preview",
                 default_timeout: Optional[float] = 1800.0,
                 **kwargs):
        """Initialize the constraint context manager.

        Args:
            base_dir: Base directory for storing constraint data
            save_path: Path to save constraint configurations
            contract_path: Path to save the constraint contract
            model_name: The model to use for the constraints
            default_timeout: Default timeout in seconds for constraint calls (None means no timeout, default 1800s = 30 minutes)
        """
        super().__init__(**kwargs)

        if base_dir is not None:
            self.base_dir = assemble_project_path(base_dir)
        else:
            self.base_dir = assemble_project_path(os.path.join(config.default_dir, "constraint"))
        logger.info(f"| 📁 Constraint context manager base directory: {self.base_dir}.")
        os.makedirs(self.base_dir, exist_ok=True)
        if save_path is not None:
            self.save_path = assemble_project_path(save_path)
        else:
            self.save_path = os.path.join(self.base_dir, "constraint.json")
        logger.info(f"| 📁 Constraint context manager save path: {self.save_path}.")
        if contract_path is not None:
            self.contract_path = assemble_project_path(contract_path)
        else:
            self.contract_path = os.path.join(self.base_dir, "contract.md")
        logger.info(f"| 📁 Constraint context manager contract path: {self.contract_path}.")

        self._constraint_configs: Dict[str, ConstraintConfig] = {}  # Current active configs (latest version)
        # Constraint version history, e.g., {"constraint_name": {"1.0.0": ConstraintConfig, "1.0.1": ConstraintConfig}}
        self._constraint_history_versions: Dict[str, Dict[str, ConstraintConfig]] = {}

        self.model_name = model_name
        self.default_timeout = default_timeout

        self._cleanup_registered = False
        self._variables_lock = asyncio.Lock()  # Lock for get/set trainable variables

    async def initialize(self, constraint_names: Optional[List[str]] = None):
        """Initialize the constraint context manager."""

        # Register constraint-related symbols for auto-injection in dynamic code
        dynamic_manager.register_symbol("CONSTRAINT", CONSTRAINT)
        dynamic_manager.register_symbol("Constraint", Constraint)
        dynamic_manager.register_symbol("Response", Response)
        dynamic_manager.register_symbol("ResponseType", ResponseType)

        # Register constraint context provider for automatic import injection
        def constraint_context_provider():
            """Provide constraint-related imports for dynamic constraint classes."""
            return {
                "CONSTRAINT": CONSTRAINT,
                "Constraint": Constraint,
                "Response": Response,
                "ResponseType": ResponseType,
            }
        dynamic_manager.register_context_provider("constraint", constraint_context_provider)

        # Load constraints from CONSTRAINT registry
        constraint_configs = {}
        registry_constraint_configs: Dict[str, ConstraintConfig] = await self._load_from_registry()
        constraint_configs.update(registry_constraint_configs)

        # Load constraints from code
        code_constraint_configs: Dict[str, ConstraintConfig] = await self._load_from_code()

        # Merge code configs with registry configs, only override if code version is strictly greater
        for constraint_name, code_config in code_constraint_configs.items():
            if constraint_name in constraint_configs:
                registry_config = constraint_configs[constraint_name]
                # Compare versions: only override if code version is strictly greater
                if version_manager.compare_versions(code_config.version, registry_config.version) > 0:
                    logger.info(f"| 🔄 Overriding constraint {constraint_name} from registry (v{registry_config.version}) with code version (v{code_config.version})")
                    constraint_configs[constraint_name] = code_config
                else:
                    logger.info(f"| 📌 Keeping constraint {constraint_name} from registry (v{registry_config.version}), code version (v{code_config.version}) is not greater")
                    # If versions are equal, update the history with registry config (which has real class, not dynamic)
                    if version_manager.compare_versions(code_config.version, registry_config.version) == 0:
                        # Replace the code config in history with registry config to preserve real class reference
                        if constraint_name in self._constraint_history_versions:
                            self._constraint_history_versions[constraint_name][registry_config.version] = registry_config
            else:
                # New constraint from code, add it
                constraint_configs[constraint_name] = code_config

        # Filter constraints by names if provided
        if constraint_names is not None:
            constraint_configs = {name: constraint_configs[name] for name in constraint_names}

        # Build all constraints concurrently with a concurrency limit
        constraint_names = list(constraint_configs.keys())
        tasks = [
            self.build(constraint_configs[name]) for name in constraint_names
        ]
        results = await gather_with_concurrency(tasks, max_concurrency=10, return_exceptions=True)

        for constraint_name, result in zip(constraint_names, results):
            if isinstance(result, Exception):
                logger.error(f"| ❌ Failed to initialize constraint {constraint_name}: {result}")
                continue
            self._constraint_configs[constraint_name] = result
            logger.info(f"| 🔒 Constraint {constraint_name} initialized")

        # Save constraint configs to json file
        await self.save_to_json()
        # Save contract to file
        await self.save_contract(constraint_names=constraint_names)

        # Register cleanup callback
        async_atexit_register(self.cleanup)
        self._cleanup_registered = True

        logger.info(f"| ✅ Constraints initialization completed")

    async def _load_from_registry(self):
        """Load constraints from CONSTRAINT registry."""

        constraint_configs: Dict[str, ConstraintConfig] = {}

        async def register_constraint_class(constraint_cls: Type[Constraint]):
            """Register a constraint class synchronously.

            Args:
                constraint_cls: Constraint class to register
            """
            try:
                # Get constraint config from global config
                constraint_config_key = inflection.underscore(constraint_cls.__name__)
                constraint_config_dict = config.get(constraint_config_key, {})
                constraint_require_grad = constraint_config_dict.get("require_grad", False) if constraint_config_dict and "require_grad" in constraint_config_dict else False

                # Get constraint properties from constraint class
                constraint_name = constraint_cls.model_fields['name'].default
                constraint_description = constraint_cls.model_fields['description'].default
                constraint_metadata = constraint_cls.model_fields['metadata'].default

                # Get or generate version from version_manager
                constraint_version = await version_manager.get_version("constraint", constraint_name)

                # Get full module source code
                constraint_code = dynamic_manager.get_full_module_source(constraint_cls)

                constraint_parameters = dynamic_manager.get_parameters(constraint_cls)
                constraint_function_calling = dynamic_manager.build_function_calling(constraint_name, constraint_description, constraint_parameters)
                constraint_text = dynamic_manager.build_text_representation(constraint_name, constraint_description, constraint_parameters, entity_type="Constraint")
                constraint_args_schema = dynamic_manager.build_args_schema(constraint_name, constraint_parameters)

                # Create constraint config
                try:
                    constraint_path = inspect.getfile(constraint_cls)
                except Exception:
                    constraint_path = None
                constraint_config = ConstraintConfig(
                    name=constraint_name,
                    description=constraint_description,
                    version=constraint_version,
                    cls=constraint_cls,
                    config=constraint_config_dict,
                    instance=None,
                    function_calling=constraint_function_calling,
                    text=constraint_text,
                    args_schema=constraint_args_schema,
                    metadata=constraint_metadata,
                    require_grad=constraint_require_grad,
                    code=constraint_code,
                    path=constraint_path,
                )

                # Store constraint config
                constraint_configs[constraint_name] = constraint_config

                # Store in version history (by version string)
                if constraint_name not in self._constraint_history_versions:
                    self._constraint_history_versions[constraint_name] = {}
                self._constraint_history_versions[constraint_name][constraint_version] = constraint_config

                # Register version to version manager
                await version_manager.register_version("constraint", constraint_name, constraint_version)

                logger.info(f"| 📝 Registered constraint: {constraint_name} ({constraint_cls.__name__})")

            except Exception as e:
                logger.error(f"| ❌ Failed to register constraint class {constraint_cls.__name__}: {e}")
                raise

        import src.constraint  # noqa: F401

        # Get all registered constraint classes from CONSTRAINT registry
        constraint_classes = list(CONSTRAINT._module_dict.values())

        logger.info(f"| 🔍 Discovering {len(constraint_classes)} constraints from CONSTRAINT registry")

        # Register each constraint class concurrently with a concurrency limit
        tasks = [
            register_constraint_class(constraint_cls) for constraint_cls in constraint_classes
        ]
        results = await gather_with_concurrency(tasks, max_concurrency=10, return_exceptions=True)
        success_count = sum(1 for r in results if not isinstance(r, Exception))

        logger.info(f"| ✅ Discovered and registered {success_count}/{len(constraint_classes)} constraints from CONSTRAINT registry")

        return constraint_configs

    async def _load_from_code(self):
        """Load constraints from code files.

        JSON file content example:
        {
            "metadata": {
                "saved_at": str,  # "YYYY-MM-DD HH:MM:SS"
                "num_constraints": int,  # total constraint count
                "num_versions": int  # total version count
            },
            "constraints": {
                "constraint_name": {
                    "current_version": "1.0.0",
                    "versions": {
                        "1.0.0": {
                            "name": str,
                            "description": str,
                            "metadata": dict,
                            "require_grad": bool,
                            "enabled": bool,
                            "version": str,
                            "cls": Type[Constraint],
                            "config": dict,
                            "instance": Constraint, # will be built when needed
                            "function_calling": dict,
                            "text": str,
                            "args_schema": BaseModel,
                            "code": str
                        },
                        ...
                    }
                }
            }
        }
        """

        constraint_configs: Dict[str, ConstraintConfig] = {}

        # If save file does not exist yet, nothing to load
        if not os.path.exists(self.save_path):
            logger.info(f"| 📂 Constraint config file not found at {self.save_path}, skipping code-based loading")
            return constraint_configs

        # Load all constraint configs from json file
        try:
            with open(self.save_path, "r", encoding="utf-8") as f:
                load_data = json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(f"| ⚠️ Failed to parse constraint config JSON from {self.save_path}: {e}")
            return constraint_configs

        metadata = load_data.get("metadata", {})
        constraints_data = load_data.get("constraints", {})

        async def register_constraint_class(constraint_name: str, constraint_data: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, ConstraintConfig], Optional[ConstraintConfig]]]:
            """Load all versions for a single constraint from JSON."""
            try:
                current_version = constraint_data.get("current_version", "1.0.0")
                versions = constraint_data.get("versions", {})

                if not versions:
                    logger.warning(f"| ⚠️ Constraint {constraint_name} has no versions")
                    return None

                version_map: Dict[str, ConstraintConfig] = {}
                current_constraint_config: Optional[ConstraintConfig] = None

                for _, version_data in versions.items():
                    constraint_config = ConstraintConfig.model_validate(version_data)
                    version = constraint_config.version
                    version_map[version] = constraint_config

                    if version == current_version:
                        current_constraint_config = constraint_config

                return constraint_name, version_map, current_constraint_config
            except Exception as e:
                logger.error(f"| ❌ Failed to load constraint {constraint_name} from code JSON: {e}")
                return None

        # Launch loading of each constraint concurrently with a concurrency limit
        tasks = [
            register_constraint_class(constraint_name, constraint_data) for constraint_name, constraint_data in constraints_data.items()
        ]
        results = await gather_with_concurrency(tasks, max_concurrency=10, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception) or result is None:
                continue
            constraint_name, version_map, current_constraint_config = result
            if not version_map:
                continue
            # Store all versions in history (mapped by version string)
            self._constraint_history_versions[constraint_name] = version_map
            # Active config: the one corresponding to current_version
            if current_constraint_config is not None:
                constraint_configs[constraint_name] = current_constraint_config
            else:
                # Fallback: if current_version is not found, use the last available version
                logger.warning(f"| ⚠️ Constraint {constraint_name} current_version not found, using last available version")
                constraint_configs[constraint_name] = list(version_map.values())[-1]

            # Register all versions to version manager
            for constraint_config in version_map.values():
                await version_manager.register_version("constraint", constraint_name, constraint_config.version)

        logger.info(f"| 📂 Loaded {len(constraint_configs)} constraints from {self.save_path}")
        return constraint_configs

    async def build(self, constraint_config: ConstraintConfig) -> ConstraintConfig:
        """Create a constraint instance and store it.

        Args:
            constraint_config: Constraint configuration

        Returns:
            ConstraintConfig: Constraint configuration with instance
        """
        if constraint_config.name in self._constraint_configs:
            existing_config = self._constraint_configs[constraint_config.name]
            if existing_config.instance is not None:
                return existing_config

        # Create new constraint instance
        try:
            # cls should already be loaded (either from registry or from code in _load_from_code)
            if constraint_config.cls is None:
                raise ValueError(f"Cannot create constraint {constraint_config.name}: no class provided. Class should be loaded during initialization.")

            # Instantiate constraint instance
            constraint_instance = constraint_config.cls(**constraint_config.config) if constraint_config.config else constraint_config.cls()

            # Initialize constraint if it has an initialize method
            if hasattr(constraint_instance, "initialize"):
                await constraint_instance.initialize()

            constraint_config.instance = constraint_instance

            # Store constraint metadata
            self._constraint_configs[constraint_config.name] = constraint_config

            logger.info(f"| 🔒 Constraint {constraint_config.name} created and stored")

            return constraint_config
        except Exception as e:
            logger.error(f"| ❌ Failed to create constraint {constraint_config.name}: {e}")
            raise

    async def register(self,
                       constraint: Constraint | Type[Constraint],
                       constraint_config_dict: Optional[Dict[str, Any]] = None,
                       override: bool = False,
                       version: Optional[str] = None,
                       code: Optional[str] = None) -> ConstraintConfig:
        """Register a constraint class or instance.

        This will:
        - Create (or reuse) a constraint instance
        - Create a `ConstraintConfig`
        - Store it as the current config and append to version history
        - Register the version in `version_manager`
        - Persist the constraint source code (if available / provided)
        """

        try:
            # Accept either a class or an already-configured instance
            if isinstance(constraint, Constraint):
                constraint_instance = constraint
                constraint_cls = type(constraint)
                if constraint_config_dict is None:
                    # Derive config from instance fields (excluding base fields)
                    base_fields = {"name", "description", "metadata", "require_grad", "enabled"}
                    constraint_config_dict = {
                        k: v for k, v in constraint_instance.model_dump().items() if k not in base_fields
                    }
            else:
                constraint_cls = constraint
                if constraint_config_dict is None:
                    # Fallback to global config by class name
                    constraint_config_key = inflection.underscore(constraint_cls.__name__)
                    constraint_config_dict = config.get(constraint_config_key, {})
                # Instantiate constraint immediately (register is a runtime operation)
                try:
                    constraint_instance = constraint_cls(**constraint_config_dict)
                except Exception as e:
                    logger.error(f"| ❌ Failed to create constraint instance for {constraint_cls.__name__}: {e}")
                    raise ValueError(f"Failed to instantiate constraint {constraint_cls.__name__} with provided config: {e}")

            constraint_name = constraint_instance.name
            constraint_description = constraint_instance.description
            constraint_metadata = constraint_instance.metadata
            # Get require_grad from constraint_config_dict if provided, otherwise from constraint_instance
            constraint_require_grad = constraint_config_dict.get("require_grad", constraint_instance.require_grad) if constraint_config_dict and "require_grad" in constraint_config_dict else constraint_instance.require_grad

            # Get or generate version from version_manager
            if version is None:
                constraint_version = await version_manager.get_version("constraint", constraint_name)
            else:
                constraint_version = version

            # Get constraint code (prefer explicit code if provided)
            constraint_code = code if code is not None else dynamic_manager.get_source_code(constraint_cls)
            if not constraint_code:
                logger.warning(f"| ⚠️ Constraint {constraint_name} is dynamic but source code cannot be extracted (and no code was provided)")

            # Get constraint parameters
            constraint_parameters = dynamic_manager.get_parameters(constraint_cls)
            constraint_function_calling = dynamic_manager.build_function_calling(constraint_name, constraint_description, constraint_parameters)
            constraint_text = dynamic_manager.build_text_representation(constraint_name, constraint_description, constraint_parameters, entity_type="Constraint")
            constraint_args_schema = dynamic_manager.build_args_schema(constraint_name, constraint_parameters)

            # --- Build ConstraintConfig ---
            try:
                constraint_path = inspect.getfile(constraint_cls)
            except Exception:
                constraint_path = None
            constraint_config = ConstraintConfig(
                name=constraint_name,
                description=constraint_description,
                metadata=constraint_metadata,
                require_grad=constraint_require_grad,
                enabled=constraint_instance.enabled,
                version=constraint_version,
                cls=constraint_cls,
                config=constraint_config_dict or {},
                instance=constraint_instance,
                function_calling=constraint_function_calling,
                text=constraint_text,
                args_schema=constraint_args_schema,
                code=constraint_code,
                path=constraint_path,
            )

            # --- Persist current config and history ---
            self._constraint_configs[constraint_name] = constraint_config

            # Store in dict-based history (for quick lookup by version)
            if constraint_name not in self._constraint_history_versions:
                self._constraint_history_versions[constraint_name] = {}
            self._constraint_history_versions[constraint_name][constraint_config.version] = constraint_config

            # Register version in version manager
            await version_manager.register_version("constraint", constraint_name, constraint_config.version)

            # Persist to JSON
            await self.save_to_json()
            # Save contract to file
            await self.save_contract()

            logger.info(f"| 📝 Registered constraint config: {constraint_name}: {constraint_config.version}")
            return constraint_config

        except Exception as e:
            logger.error(f"| ❌ Failed to register constraint: {e}")
            raise

    async def get(self, constraint_name: str) -> Optional[Constraint]:
        """Get constraint instance by name

        Args:
            constraint_name: Constraint name

        Returns:
            Constraint: Constraint instance or None if not found
        """
        constraint_config = self._constraint_configs.get(constraint_name)
        if constraint_config is None:
            return None
        return constraint_config.instance if constraint_config.instance is not None else None

    async def get_info(self, constraint_name: str) -> Optional[ConstraintConfig]:
        """Get constraint info by name

        Args:
            constraint_name: Constraint name

        Returns:
            ConstraintConfig: Constraint info or None if not found
        """
        return self._constraint_configs.get(constraint_name)

    async def list(self) -> List[str]:
        """Get list of registered constraints

        Returns:
            List[str]: List of constraint names
        """
        return [name for name in self._constraint_configs.keys()]

    async def update(self,
                     constraint: Constraint | Type[Constraint],
                     constraint_config_dict: Optional[Dict[str, Any]] = None,
                     new_version: Optional[str] = None,
                     description: Optional[str] = None,
                     code: Optional[str] = None) -> ConstraintConfig:
        """Update an existing constraint with new configuration and create a new version

        Args:
            constraint: New constraint class or instance with updated implementation
            constraint_config_dict: Configuration dict for constraint initialization
                   If None, will try to get from global config
            new_version: New version string. If None, auto-increments from current version.
            description: Description for this version update
            code: Optional source code string. If provided, uses this instead of extracting from constraint_cls.
                  This is useful when constraint_cls is dynamically created from code string.

        Returns:
            ConstraintConfig: Updated constraint configuration
        """
        try:
            # Accept either a class or an already-configured instance
            if isinstance(constraint, Constraint):
                constraint_instance = constraint
                constraint_cls = type(constraint)
                if constraint_config_dict is None:
                    base_fields = {"name", "description", "metadata", "require_grad", "enabled"}
                    constraint_config_dict = {
                        k: v for k, v in constraint_instance.model_dump().items() if k not in base_fields
                    }
            else:
                constraint_cls = constraint
                if constraint_config_dict is None:
                    # Fallback to global config by class name
                    constraint_config_key = inflection.underscore(constraint_cls.__name__)
                    constraint_config_dict = config.get(constraint_config_key, {})
                # Instantiate constraint immediately (update is a runtime operation)
                try:
                    constraint_instance = constraint_cls(**constraint_config_dict)
                except Exception as e:
                    logger.error(f"| ❌ Failed to create constraint instance for {constraint_cls.__name__}: {e}")
                    raise ValueError(f"Failed to instantiate constraint {constraint_cls.__name__} with provided config: {e}")

            constraint_name = constraint_instance.name

            # Check if constraint exists
            original_config = self._constraint_configs.get(constraint_name)
            if original_config is None:
                raise ValueError(f"Constraint {constraint_name} not found. Use register() to register a new constraint.")

            constraint_description = constraint_instance.description
            constraint_metadata = constraint_instance.metadata
            # Get require_grad from constraint_config_dict if provided, otherwise from constraint_instance
            constraint_require_grad = constraint_config_dict.get("require_grad", constraint_instance.require_grad) if constraint_config_dict and "require_grad" in constraint_config_dict else constraint_instance.require_grad

            # Determine new version from version_manager
            if new_version is None:
                # Get current version from version_manager and generate next patch version
                new_version = await version_manager.generate_next_version("constraint", constraint_name, "patch")

            # Get constraint code - use provided code if available (for dynamically created classes)
            if code is not None:
                constraint_code = code
            else:
                constraint_code = dynamic_manager.get_source_code(constraint_cls)
                if not constraint_code:
                    logger.warning(f"| ⚠️ Constraint {constraint_name} is dynamic but source code cannot be extracted")

            # Get constraint parameters and build properties using dynamic_manager methods
            constraint_parameters = dynamic_manager.get_parameters(constraint_cls)
            constraint_function_calling = dynamic_manager.build_function_calling(constraint_name, constraint_description, constraint_parameters)
            constraint_text = dynamic_manager.build_text_representation(constraint_name, constraint_description, constraint_parameters, entity_type="Constraint")
            constraint_args_schema = dynamic_manager.build_args_schema(constraint_name, constraint_parameters)

            # --- Build ConstraintConfig ---
            updated_config = ConstraintConfig(
                name=constraint_name,  # Keep same name
                description=constraint_description,
                metadata=constraint_metadata,
                require_grad=constraint_require_grad,
                enabled=constraint_instance.enabled,
                version=new_version,
                cls=constraint_cls,
                config=constraint_config_dict or {},
                instance=constraint_instance,
                function_calling=constraint_function_calling,
                text=constraint_text,
                args_schema=constraint_args_schema,
                code=constraint_code,
            )

            # Update the constraint config (replaces current version)
            self._constraint_configs[constraint_name] = updated_config

            # Store in version history
            if constraint_name not in self._constraint_history_versions:
                self._constraint_history_versions[constraint_name] = {}
            self._constraint_history_versions[constraint_name][updated_config.version] = updated_config

            # Register new version record to version manager
            await version_manager.register_version(
                "constraint",
                constraint_name,
                new_version,
                description=description or f"Updated from {original_config.version}"
            )

            # Persist to JSON
            await self.save_to_json()
            # Save contract to file
            await self.save_contract()

            logger.info(f"| 🔄 Updated constraint {constraint_name} from v{original_config.version} to v{new_version}")
            return updated_config

        except Exception as e:
            logger.error(f"| ❌ Failed to update constraint: {e}")
            raise

    async def copy(self,
                  constraint_name: str,
                  new_name: Optional[str] = None,
                  new_version: Optional[str] = None,
                  new_config: Optional[Dict[str, Any]] = None) -> ConstraintConfig:
        """Copy an existing constraint configuration

        Args:
            constraint_name: Name of the constraint to copy
            new_name: New name for the copied constraint. If None, uses original name.
            new_version: New version for the copied constraint. If None, increments version.
            new_config: New configuration dict for the copied constraint. If None, uses original config.

        Returns:
            ConstraintConfig: New constraint configuration
        """
        try:
            original_config = self._constraint_configs.get(constraint_name)
            if original_config is None:
                raise ValueError(f"Constraint {constraint_name} not found")

            if original_config.cls is None:
                raise ValueError(f"Cannot copy constraint {constraint_name}: no class provided")

            # Determine new name
            if new_name is None:
                new_name = constraint_name

            # Prepare config dict (merge original config with new config)
            constraint_config_dict = original_config.config.copy() if original_config.config else {}
            if new_config:
                # Merge new config into original config
                constraint_config_dict.update(new_config)

            # Instantiate constraint instance (copy is a runtime operation)
            try:
                constraint_instance = original_config.cls(**constraint_config_dict)
            except Exception as e:
                logger.error(f"| ❌ Failed to create constraint instance for {original_config.cls.__name__}: {e}")
                raise ValueError(f"Failed to instantiate constraint {original_config.cls.__name__} with provided config: {e}")

            # Apply name override if provided (after instantiation)
            if new_name != constraint_name:
                constraint_instance.name = new_name

            constraint_description = constraint_instance.description
            constraint_metadata = constraint_instance.metadata
            constraint_require_grad = constraint_config_dict.get("require_grad", constraint_instance.require_grad) if constraint_config_dict and "require_grad" in constraint_config_dict else constraint_instance.require_grad

            # Determine new version from version_manager
            if new_version is None:
                if new_name == constraint_name:
                    # If copying with same name, get next version from version_manager
                    new_version = await version_manager.generate_next_version("constraint", new_name, "patch")
                else:
                    # If copying with different name, get or generate version for new name
                    new_version = await version_manager.get_version("constraint", new_name)

            # Get constraint code
            constraint_code = dynamic_manager.get_source_code(original_config.cls)
            if not constraint_code:
                logger.warning(f"| ⚠️ Constraint {new_name} is dynamic but source code cannot be extracted")

            # Get constraint parameters and build properties using dynamic_manager methods
            constraint_parameters = dynamic_manager.get_parameters(original_config.cls)
            constraint_function_calling = dynamic_manager.build_function_calling(new_name, constraint_description, constraint_parameters)
            constraint_text = dynamic_manager.build_text_representation(new_name, constraint_description, constraint_parameters, entity_type="Constraint")
            constraint_args_schema = dynamic_manager.build_args_schema(new_name, constraint_parameters)

            # --- Build ConstraintConfig ---
            new_config = ConstraintConfig(
                name=new_name,
                description=constraint_description,
                metadata=constraint_metadata,
                require_grad=constraint_require_grad,
                enabled=constraint_instance.enabled,
                version=new_version,
                cls=original_config.cls,
                config=constraint_config_dict,
                instance=constraint_instance,
                function_calling=constraint_function_calling,
                text=constraint_text,
                args_schema=constraint_args_schema,
                code=constraint_code,
            )

            # Register new constraint
            self._constraint_configs[new_name] = new_config

            # Store in version history
            if new_name not in self._constraint_history_versions:
                self._constraint_history_versions[new_name] = {}
            self._constraint_history_versions[new_name][new_version] = new_config

            # Register version record to version manager
            await version_manager.register_version(
                "constraint",
                new_name,
                new_version,
                description=f"Copied from {constraint_name}@{original_config.version}"
            )

            # Persist to JSON
            await self.save_to_json()
            # Save contract to file
            await self.save_contract()

            logger.info(f"| 📋 Copied constraint {constraint_name}@{original_config.version} to {new_name}@{new_version}")
            return new_config

        except Exception as e:
            logger.error(f"| ❌ Failed to copy constraint: {e}")
            raise

    async def unregister(self, constraint_name: str) -> bool:
        """Unregister a constraint

        Args:
            constraint_name: Name of the constraint to unregister

        Returns:
            True if unregistered successfully, False otherwise
        """
        if constraint_name not in self._constraint_configs:
            logger.warning(f"| ⚠️ Constraint {constraint_name} not found")
            return False

        constraint_config = self._constraint_configs[constraint_name]

        # Remove from configs
        del self._constraint_configs[constraint_name]

        # Persist to JSON after unregister
        await self.save_to_json()
        # Save contract to file
        await self.save_contract()

        logger.info(f"| 🗑️ Unregistered constraint {constraint_name}@{constraint_config.version}")
        return True

    async def save_to_json(self, file_path: Optional[str] = None) -> str:
        """Save all constraint configurations with version history to JSON.

        Only saves basic configuration fields (name, description, version, config, etc.).
        Instance is not saved as it's runtime state and will be recreated via build() on load.

        Args:
            file_path: File path to save to

        Returns:
            Path to saved file
        """
        file_path = file_path if file_path is not None else self.save_path

        async with file_lock(file_path):
            # Ensure parent directory exists
            parent_dir = os.path.dirname(file_path)
            if parent_dir:  # Only create if there's a directory component
                os.makedirs(parent_dir, exist_ok=True)

            # Prepare save data - save all versions for each constraint
            save_data = {
                "metadata": {
                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "num_constraints": len(self._constraint_configs),
                    "num_versions": sum(len(versions) for versions in self._constraint_history_versions.values()),
                },
                "constraints": {}
            }

            for constraint_name, version_map in self._constraint_history_versions.items():
                try:
                    versions_data: Dict[str, Dict[str, Any]] = {}
                    for _, constraint_config in version_map.items():
                        config_dict = constraint_config.model_dump()
                        versions_data[constraint_config.version] = config_dict

                    # Get current_version from active config if it exists
                    # If not in active configs, use the latest version from history
                    current_version = None
                    if constraint_name in self._constraint_configs:
                        current_config = self._constraint_configs[constraint_name]
                        if current_config is not None:
                            current_version = current_config.version

                    # If not found in active configs, use latest version from history
                    if current_version is None and version_map:
                        # Find latest version by comparing version strings
                        latest_version_str = None
                        for version_str in version_map.keys():
                            if latest_version_str is None:
                                latest_version_str = version_str
                            elif version_manager.compare_versions(version_str, latest_version_str) > 0:
                                latest_version_str = version_str
                        current_version = latest_version_str

                    save_data["constraints"][constraint_name] = {
                        "versions": versions_data,
                        "current_version": current_version
                    }
                except Exception as e:
                    logger.warning(f"| ⚠️ Failed to serialize constraint {constraint_name}: {e}")
                    continue

            # Save to file
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=4, ensure_ascii=False)

            logger.info(f"| 💾 Saved {len(self._constraint_configs)} constraints with version history to {file_path}")
            return str(file_path)

    async def load_from_json(self, file_path: Optional[str] = None, auto_initialize: bool = True) -> bool:
        """Load constraint configurations with version history from JSON.

        Loads basic configuration only (instance is not saved, must be created via build()).
        Only the latest version will be instantiated by default if auto_initialize=True.

        Args:
            file_path: File path to load from
            auto_initialize: Whether to automatically create instance via build() after loading

        Returns:
            True if loaded successfully, False otherwise
        """

        file_path = file_path if file_path is not None else self.save_path

        async with file_lock(file_path):
            if not os.path.exists(file_path):
                logger.warning(f"| ⚠️ Constraint file not found: {file_path}")
                return False

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    load_data = json.load(f)

                constraints_data = load_data.get("constraints", {})
                loaded_count = 0

                for constraint_name, constraint_data in constraints_data.items():
                    try:
                        # Expected format: multiple versions stored as a dict {version_str: config_dict}
                        versions_data = constraint_data.get("versions")
                        if not isinstance(versions_data, dict):
                            logger.warning(f"| ⚠️ Constraint {constraint_name} has invalid format for 'versions' (expected dict), skipping")
                            continue

                        current_version_str = constraint_data.get("current_version")

                        # Load all versions
                        version_configs = []
                        latest_config = None
                        latest_version = None

                        for version_str, config_dict in versions_data.items():
                            # Ensure version field is present
                            if "version" not in config_dict:
                                config_dict["version"] = version_str

                            try:
                                constraint_config = ConstraintConfig.model_validate(config_dict)
                                version_configs.append(constraint_config)
                            except Exception as e:
                                logger.warning(f"| ⚠️ Failed to load constraint config for {constraint_name}@{version_str}: {e}")
                                continue

                            # Track latest version
                            if latest_config is None or (
                                current_version_str and constraint_config.version == current_version_str
                            ) or (
                                not current_version_str and (
                                    latest_version is None or
                                    version_manager.compare_versions(constraint_config.version, latest_version) > 0
                                )
                            ):
                                latest_config = constraint_config
                                latest_version = constraint_config.version

                        # Store all versions in history (dict-based)
                        self._constraint_history_versions[constraint_name] = {
                            cfg.version: cfg for cfg in version_configs
                        }

                        # Only set latest version as active
                        if latest_config:
                            self._constraint_configs[constraint_name] = latest_config

                            # Register all versions to version manager (only version records)
                            for constraint_config in version_configs:
                                await version_manager.register_version("constraint", constraint_name, constraint_config.version)

                            # Create instance if requested (instance is not saved in JSON, must be created via build)
                            if auto_initialize and latest_config.cls is not None:
                                await self.build(latest_config)

                            loaded_count += 1
                    except Exception as e:
                        logger.error(f"| ❌ Failed to load constraint {constraint_name}: {e}")
                        continue

                logger.info(f"| 📂 Loaded {loaded_count} constraints with version history from {file_path}")
                return True

            except Exception as e:
                logger.error(f"| ❌ Failed to load constraints from {file_path}: {e}")
                return False

    async def restore(self, constraint_name: str, version: str, auto_initialize: bool = True) -> Optional[ConstraintConfig]:
        """Restore a specific version of a constraint from history

        Args:
            constraint_name: Name of the constraint
            version: Version string to restore
            auto_initialize: Whether to automatically initialize the restored constraint

        Returns:
            ConstraintConfig of the restored version, or None if not found
        """
        # Look up version from dict-based history (O(1) lookup)
        version_config = None
        if constraint_name in self._constraint_history_versions:
            version_config = self._constraint_history_versions[constraint_name].get(version)

        if version_config is None:
            logger.warning(f"| ⚠️ Version {version} not found for constraint {constraint_name}")
            return None

        # Create a copy to avoid modifying the history (preserves the real cls reference)
        restored_config = version_config.model_copy()

        # Set as current active config
        self._constraint_configs[constraint_name] = restored_config

        # Update version manager current version
        version_history = await version_manager.get_version_history("constraint", constraint_name)
        if version_history:
            # Check if version exists in version history, if not register it
            if version not in version_history.versions:
                await version_manager.register_version("constraint", constraint_name, version)
            version_history.current_version = version
        else:
            # If version history doesn't exist, register the version first
            await version_manager.register_version("constraint", constraint_name, version)

        # Initialize if requested
        if auto_initialize and restored_config.cls is not None:
            await self.build(restored_config)

        # Persist to JSON (current_version changes)
        await self.save_to_json()

        logger.info(f"| 🔄 Restored constraint {constraint_name} to version {version}")
        return restored_config

    async def save_contract(self, constraint_names: Optional[List[str]] = None):
        """Save the contract for constraints"""
        contract = []
        if constraint_names is not None:
            for index, constraint_name in enumerate(constraint_names):
                constraint_info = await self.get_info(constraint_name)
                if constraint_info is None:
                    logger.warning(f"| ⚠️ Constraint '{constraint_name}' not found in registry, skipping")
                    continue
                text = constraint_info.text
                contract.append(f"{index + 1:04d}\n{text}\n")
        else:
            for index, constraint_name in enumerate(self._constraint_configs.keys()):
                constraint_info = await self.get_info(constraint_name)
                if constraint_info is None:
                    logger.warning(f"| ⚠️ Constraint '{constraint_name}' not found in registry, skipping")
                    continue
                text = constraint_info.text
                contract.append(f"{index + 1:04d}\n{text}\n")
        contract_text = "---\n".join(contract)
        with open(self.contract_path, "w", encoding="utf-8") as f:
            f.write(contract_text)
        logger.info(f"| 📝 Saved {len(contract)} constraints contract to {self.contract_path}")

    async def load_contract(self) -> str:
        """Load the contract for constraints"""
        with open(self.contract_path, "r", encoding="utf-8") as f:
            contract_text = f.read()
        return contract_text

    async def cleanup(self):
        """Cleanup all active constraints."""
        try:
            # Clear all constraint configs and version history
            self._constraint_configs.clear()
            self._constraint_history_versions.clear()

            logger.info("| 🧹 Constraint context manager cleaned up")

        except Exception as e:
            logger.error(f"| ❌ Error during constraint context manager cleanup: {e}")

    async def __call__(self,
                       name: str,
                       input: Dict[str, Any],
                       ctx: ConstraintContext = None,
                       **kwargs
                       ) -> Response:
        """Call a constraint by name with optional timeout

        Args:
            name: Constraint name
            input: Input for the constraint
            ctx: Optional constraint context to pass to the constraint
        Returns:
            Response: Constraint result
        """

        constraint_info = await self.get_info(name)

        if constraint_info is None:
            error_msg = f"Constraint '{name}' is not registered. Available constraints: {list(self._constraint_configs.keys())}"
            logger.error(f"| ❌ {error_msg}")
            return Response(type=ResponseType.CONSTRAINT, success=False, message=error_msg)

        version = constraint_info.version
        constraint_instance = constraint_info.instance
        logger.info(f"| ✅ Using constraint {name}@{version}")

        # Disabled constraints always pass
        if not constraint_instance.enabled:
            return Response(type=ResponseType.CONSTRAINT, success=True, message="")

        # Use asyncio.wait_for to enforce timeout
        try:
            result = await asyncio.wait_for(constraint_instance(input, ctx), timeout=self.default_timeout)
        except asyncio.TimeoutError:
            error_msg = f"Constraint '{name}' execution timed out after {self.default_timeout} seconds"
            logger.error(f"| ⏱️ {error_msg}")
            return Response(
                type=ResponseType.CONSTRAINT,
                success=False,
                message=error_msg,
            )

        if not result.success:
            logger.warning(f"| 🚫 Constraint violated [{name}] task_id={ctx.id}: {result.message}")
        return result
