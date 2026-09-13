"""Environment Context Manager for managing environment lifecycle and resources with lazy loading."""

import os
import re
import json
import inspect
import inflection
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List, Type, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field

from asyncio_atexit import register as async_atexit_register

from agentevolver.paths import P, path_manager
from agentevolver.logger import logger
from agentevolver.config import config
from agentevolver.version import version_manager
from agentevolver.utils import assemble_workspace_path, gather_with_concurrency
from agentevolver.utils.file_utils import file_lock
from agentevolver.environment.types import Environment, EnvironmentConfig, ActionConfig, EnvironmentContext
from agentevolver.sandbox import SandboxServerManager
from agentevolver.dynamic import dynamic_manager
from agentevolver.registry import ENVIRONMENT
from agentevolver.permission import PermissionMode

from agentevolver.permission import EffectContract, Operation, PermissionRequest, permission_manager
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.execution import ToolExecution, ToolExecutionPipeline, ToolPolicyDecision

class EnvironmentContextManager(BaseModel):
    """Global context manager for all environments with lazy loading support."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    
    base_dir: str = Field(default=None, description="The base directory to use for the environments")
            
    def __init__(self,
                 base_dir: Optional[str] = None,
                 **kwargs):
        """Initialize the environment context manager.

        Args:
            base_dir: Base directory for storing environment data
        """
        super().__init__(**kwargs)
        self._execution_pipeline = ToolExecutionPipeline(capability_type="environment")
        
        # Set up paths
        if base_dir is not None:
            self.base_dir = assemble_workspace_path(base_dir)
        else:
            base_root = config.log_root if hasattr(config, "log_root") and config.get("log_root") else config.workspace_root
            self.base_dir = assemble_workspace_path(path_manager.under(base_root, P.LOG_MODULE, module="environment"))
        logger.info(f"| 📁 Environment context manager base directory: {self.base_dir}.")    
        logger.info(f"| 📁 Environment context manager.")

        self._environment_configs: Dict[str, EnvironmentConfig] = {}  # Current active configs (latest version)
        # Environment version history, e.g., {"env_name": {"1.0.0": EnvironmentConfig, "1.0.1": EnvironmentConfig}}
        self._environment_history_versions: Dict[str, Dict[str, EnvironmentConfig]] = {}

        # Daemon domain comes from the port manager (preferring OPENSANDBOX, else a
        # free port) rather than a hard-coded 8080, so it can't collide on a shared
        # host — but it is resolved on first use, not here. Whether the daemon is
        # needed at all is decided later, in `_ensure_sandbox_server`, from whether
        # any registered environment asks for `use_sandbox`; most rosters do not,
        # and constructing this manager must not claim a port for a daemon that
        # never starts.
        self._sandbox_server = SandboxServerManager()
        self._cleanup_registered = False
        
    async def initialize(self, env_names: Optional[List[str]] = None):
        """Initialize the environment context manager."""

        await version_manager.initialize()

        # Register environment-related symbols for auto-injection in dynamic code
        dynamic_manager.register_symbol("ENVIRONMENT", ENVIRONMENT)
        dynamic_manager.register_symbol("Environment", Environment)
        dynamic_manager.register_symbol("EnvironmentConfig", EnvironmentConfig)
        dynamic_manager.register_symbol("ActionConfig", ActionConfig)
        
        # Register environment context provider for automatic import injection
        def environment_context_provider():
            """Provide environment-related imports for dynamic environment classes."""
            return {
                "ENVIRONMENT": ENVIRONMENT,
                "Environment": Environment,
                "EnvironmentConfig": EnvironmentConfig,
                "ActionConfig": ActionConfig,
            }
        dynamic_manager.register_context_provider("environment", environment_context_provider)
        
        # Load environments from ENVIRONMENT registry
        env_configs = {}
        registry_env_configs: Dict[str, EnvironmentConfig] = await self._load_from_registry()
        env_configs.update(registry_env_configs)
        
        # Load environments from code
        code_configs: Dict[str, EnvironmentConfig] = {}
        
        # Merge code configs with registry configs, only override if code version is strictly greater
        for env_name, code_config in code_configs.items():
            if env_name in env_configs:
                registry_config = env_configs[env_name]
                # Compare versions: only override if code version is strictly greater
                if version_manager.compare_versions(code_config.version, registry_config.version) > 0:
                    logger.info(f"| 🔄 Overriding environment {env_name} from registry (v{registry_config.version}) with code version (v{code_config.version})")
                    env_configs[env_name] = code_config
                else:
                    logger.info(f"| 📌 Keeping environment {env_name} from registry (v{registry_config.version}), code version (v{code_config.version}) is not greater")
                    # If versions are equal, update the history with registry config (which has real class, not dynamic)
                    if version_manager.compare_versions(code_config.version, registry_config.version) == 0:
                        # Replace the code config in history with registry config to preserve real class reference
                        if env_name in self._environment_history_versions:
                            self._environment_history_versions[env_name][registry_config.version] = registry_config
            else:
                # New environment from code, add it
                env_configs[env_name] = code_config
        
        # Filter environments by names if provided
        if env_names is not None:
            env_configs = {name: env_configs[name] for name in env_names if name in env_configs}
        
        # Start opensandbox-server once if any environment requires it
        await self._ensure_sandbox_server(env_configs)

        # Build all environments concurrently with a concurrency limit
        env_names_list = list(env_configs.keys())
        tasks = [
            self.build(env_configs[name]) for name in env_names_list
        ]
        results = await gather_with_concurrency(tasks, max_concurrency=10, return_exceptions=True)

        for env_name, result in zip(env_names_list, results):
            if isinstance(result, Exception):
                logger.error(f"| ❌ Failed to initialize environment {env_name}: {result}")
                continue
            self._environment_configs[env_name] = result
            logger.info(f"| 🎮 Environment {env_name} initialized")
        
        # Save environment configs to json file
        # Save contract to file
        
        # Register cleanup callback
        async_atexit_register(self.cleanup)
        self._cleanup_registered = True
        
        logger.info(f"| ✅ Environments initialization completed")
    
    # ------------------------------------------------------------------
    # ENVIRONMENT.md parsing (rules + docs live in the md, not in code)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
        """Split YAML frontmatter (between --- delimiters) from the markdown body."""
        pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
        match = pattern.match(text)
        if not match:
            return {}, text
        try:
            frontmatter = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as e:
            logger.warning(f"| ⚠️ Failed to parse ENVIRONMENT.md frontmatter: {e}")
            frontmatter = {}
        if not isinstance(frontmatter, dict):
            frontmatter = {}
        return frontmatter, text[match.end():]

    def _load_environment_md(self, env_cls: Type[Environment]) -> Optional[Tuple[Dict[str, Any], str, str]]:
        """Locate and parse the ENVIRONMENT.md that sits next to an environment class.

        Returns (frontmatter, body, md_path) or None if the class has no source file
        or no ENVIRONMENT.md beside it (e.g. dynamically generated environments).
        """
        try:
            env_file = inspect.getfile(env_cls)
        except (TypeError, OSError):
            env_file = getattr(env_cls, "__source_file__", None)
        if not env_file:
            return None
        md_path = Path(env_file).parent / "ENVIRONMENT.md"
        if not md_path.exists():
            return None
        try:
            frontmatter, body = self._parse_frontmatter(md_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"| ⚠️ Failed to read {md_path}: {e}")
            return None
        return frontmatter, body.strip(), str(md_path)

    async def _load_from_registry(self):
        """Load environments from ENVIRONMENT registry."""
        
        env_configs: Dict[str, EnvironmentConfig] = {}
        
        async def register_environment_class(env_cls: Type[Environment]):
            """Register an environment class.
            
            Args:
                env_cls: Environment class to register
            """
            try:
                # Get environment properties from environment class
                env_name = env_cls.model_fields['name'].default

                # A config block may be keyed by either the underscored class name or the
                # environment's registered name. They coincide for `BrowserEnvironment` /
                # `browser_environment`, which is why only the first was ever needed — but
                # an environment whose name says what it is rather than how it is reached
                # (`SSHEnvironment` serving `remote_host`) makes them differ, and the
                # config that named the block after `env_names` was silently ignored.
                env_config_key = inflection.underscore(env_cls.__name__)
                env_config_dict = config.get(env_config_key, {}) or config.get(env_name, {})
                env_enable_evolving = env_config_dict.get("enable_evolving", False) if env_config_dict and "enable_evolving" in env_config_dict else False

                env_description = env_cls.model_fields['description'].default
                env_metadata = env_cls.model_fields['metadata'].default
                env_permission_mode = env_config_dict.get(
                    "permission_mode",
                    env_cls.model_fields["permission_mode"].default,
                )

                # Rules + docs come from the ENVIRONMENT.md beside the class (not get_rules()).
                env_rules = ""
                env_manifest_path = ""
                md = self._load_environment_md(env_cls)
                if md:
                    frontmatter, body, env_manifest_path = md
                    env_description = frontmatter.get("description", env_description)
                    env_rules = body
                else:
                    logger.warning(f"| ⚠️ No ENVIRONMENT.md found for environment '{env_name}'; rules will be empty")

                # Get or generate version from version_manager
                env_version = await version_manager.get_version("environment", env_name)
                
                # Get full module source code
                env_code = dynamic_manager.get_full_module_source(env_cls)
                
                # Build actions from environment class
                env_actions = {}
                for attr_name in dir(env_cls):
                    attr = getattr(env_cls, attr_name)
                    if hasattr(attr, '_action_name'):
                        action_name = getattr(attr, '_action_name')
                        action_description = getattr(attr, '_action_description', '')
                        action_function = getattr(attr, '_action_function', None)
                        action_metadata = getattr(attr, '_action_metadata', {})
                        
                        action_version = await version_manager.get_version("action", action_name)
                        
                        action_code = dynamic_manager.get_source_code(attr)
                        if not action_code:
                            logger.warning(f"| ⚠️ Action {action_name} is dynamic but source code cannot be extracted")
                        
                        action_parameters = dynamic_manager.get_parameters(action_function)
                        action_function_calling = dynamic_manager.build_function_calling(action_name, action_description, action_parameters)
                        action_text = dynamic_manager.build_text_representation(action_name, action_description, action_parameters)
                        action_args_schema = dynamic_manager.build_args_schema(action_name, action_parameters)
                        
                        action_config = ActionConfig(
                            env_name=env_name,
                            name=action_name,
                            description=action_description,
                            function=action_function,
                            metadata=action_metadata,
                            version=action_version,
                            code=action_code,
                            function_calling=action_function_calling,
                            text=action_text,
                            args_schema=action_args_schema,
                        )
                        
                        env_actions[action_name] = action_config
                        
                        
                # Build environment config
                env_config = EnvironmentConfig(
                    name=env_name,
                    description=env_description,
                    metadata=env_metadata,
                    version=env_version,
                    enable_evolving=env_enable_evolving,
                    permission_mode=env_permission_mode,
                    cls=env_cls,
                    config=env_config_dict,
                    instance=None,
                    code=env_code,
                    actions=env_actions,
                    rules=env_rules,  # from ENVIRONMENT.md body
                    manifest_path=env_manifest_path,
                )
                
                env_configs[env_name] = env_config
                
                # Store in dict-based history (for quick lookup by version)
                if env_name not in self._environment_history_versions:
                    self._environment_history_versions[env_name] = {}
                self._environment_history_versions[env_name][env_version] = env_config
                
                # Register version to version manager
                await version_manager.register_version("environment", env_name, env_version)
                
                logger.info(f"| 📝 Registered environment: {env_name} ({env_cls.__name__})")

            except Exception as e:
                logger.error(f"| ❌ Failed to register environment class {env_cls.__name__}: {e}")
                raise
            
        import agentevolver.environment  # noqa: F401
        
        # Get all registered environment classes from ENVIRONMENT registry
        environment_classes = list(ENVIRONMENT._module_dict.values())
        
        logger.info(f"| 🔍 Discovering {len(environment_classes)} environments from ENVIRONMENT registry")
        
        # Register each environment class concurrently with a concurrency limit
        tasks = [
            register_environment_class(env_cls) for env_cls in environment_classes
        ]
        results = await gather_with_concurrency(tasks, max_concurrency=10, return_exceptions=True)
        success_count = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        
        logger.info(f"| ✅ Discovered and registered {success_count}/{len(environment_classes)} environments from ENVIRONMENT registry")
        
        return env_configs
    
    async def build(self, env_config: EnvironmentConfig) -> EnvironmentConfig:
        """Build an environment instance from config (internal helper, similar to tool's build).
        
        Args:
            env_config: Environment configuration
            
        Returns:
            EnvironmentConfig: Environment configuration with instance
        """
        if env_config.name in self._environment_configs:
            existing_config = self._environment_configs[env_config.name]
            if existing_config.instance is not None:
                return existing_config
        
        try:
            if env_config.cls is None:
                raise ValueError(f"Cannot create environment {env_config.name}: no class provided. Class should be loaded during initialization.")
            
            env_instance = env_config.cls(**env_config.config) if env_config.config else env_config.cls()
            
            # Initialize environment if it has an initialize method
            if (env_instance.managed_sessions or env_instance.state_scope == "shared") and hasattr(env_instance, "initialize"):
                await env_instance.initialize()

            # Initialization may disable actions whose optional service is unavailable.
            # Keep discovery, schemas and dispatch on that same executable subset.
            env_config.actions = {name: action for name, action in env_config.actions.items()
                                  if name in env_instance.actions}
                
            env_config.instance = env_instance
            permission_manager.register(
                entity_name=env_instance.name,
                mode=PermissionMode(env_config.permission_mode),
            )

            # Rules come from ENVIRONMENT.md (loaded at registration) — no code-generated fallback.
            if not env_config.rules:
                logger.warning(f"| ⚠️ Environment {env_config.name} has empty rules (missing ENVIRONMENT.md?)")

            # Store metadata
            self._environment_configs[env_config.name] = env_config
            
            logger.info(f"| ✅ Environment {env_config.name} created and stored")
            
            return env_config
        except Exception as e:
            logger.error(f"| ❌ Failed to create environment {env_config.name}: {e}")
            raise
    
    async def register(self, 
                       env_cls: Type[Environment], 
                       env_config_dict: Optional[Dict[str, Any]] = None,
                       override: bool = False,
                       version: Optional[str] = None) -> EnvironmentConfig:
        """Register an environment class.
        
        This will:
        - Create an environment instance
        - Create an `EnvironmentConfig`
        - Store it as the current config and append to version history
        - Register the version in `version_manager` and FAISS index
        
        Args:
            env_cls: Environment class
            env_config_dict: Configuration dict for environment initialization.
                           If None, will try to get from global config or use empty dict.
            override: Whether to override existing registration
            version: Optional version string. If None, auto-generates from version_manager.
            
        Returns:
            EnvironmentConfig: Environment configuration
        """
        try:
            if env_config_dict is None:
                # Fallback to global config by class name
                env_config_key = inflection.underscore(env_cls.__name__)
                env_config_dict = getattr(config, env_config_key, {}) if hasattr(config, env_config_key) else {}
            
            # Ensure opensandbox-server is running if this environment needs it
            if env_config_dict and env_config_dict.get("use_sandbox", False):
                await self._ensure_sandbox_server({}, force=True)

            # Instantiate environment immediately (register is a runtime operation)
            try:
                env_instance = env_cls(**env_config_dict)
            except Exception as e:
                logger.error(f"| ❌ Failed to create environment instance for {env_cls.__name__}: {e}")
                raise ValueError(f"Failed to instantiate environment {env_cls.__name__} with provided config: {e}")
            
            env_name = env_instance.name
            env_description = env_instance.description
            env_metadata = getattr(env_instance, 'metadata', {})
            env_enable_evolving = getattr(env_instance, 'enable_evolving', False)
            env_permission_mode = getattr(env_instance, 'permission_mode', 'workspace_write')
            
            if not env_name:
                raise ValueError("Environment.name cannot be empty.")
            
            if env_name in self._environment_configs and not override:
                raise ValueError(f"Environment '{env_name}' already registered. Use override=True to replace it.")
            
            # Get or generate version from version_manager
            if version is None:
                env_version = await version_manager.get_version("environment", env_name)
            else:
                env_version = version
            
            # Get environment code
            env_code = dynamic_manager.get_full_module_source(env_cls)
            
            # Build actions from environment class (same as _load_from_registry)
            actions = {}
            for attr_name in dir(env_cls):
                attr = getattr(env_cls, attr_name)
                if hasattr(attr, '_action_name'):
                    action_name = getattr(attr, '_action_name')
                    action_description = getattr(attr, '_action_description', '')
                    action_function = getattr(attr, '_action_function', None)
                    action_metadata = getattr(attr, '_action_metadata', {})
                    
                    action_version = await version_manager.get_version("action", action_name)
                    
                    action_code = dynamic_manager.get_source_code(attr)
                    if not action_code:
                        logger.warning(f"| ⚠️ Action {action_name} is dynamic but source code cannot be extracted")
                    
                    action_parameters = dynamic_manager.get_parameters(action_function)
                    action_function_calling = dynamic_manager.build_function_calling(action_name, action_description, action_parameters)
                    action_text = dynamic_manager.build_text_representation(action_name, action_description, action_parameters)
                    action_args_schema = dynamic_manager.build_args_schema(action_name, action_parameters)
                    
                    action_config = ActionConfig(
                        env_name=env_name,
                        name=action_name,
                        description=action_description,
                        function=action_function,
                        metadata=action_metadata,
                        version=action_version,
                        code=action_code,
                        function_calling=action_function_calling,
                        text=action_text,
                        args_schema=action_args_schema,
                    )
                    
                    actions[action_name] = action_config
            
            # Rules from the ENVIRONMENT.md beside the class (no code-generated get_rules)
            _env_md = self._load_environment_md(type(env_instance))
            env_rules = _env_md[1] if _env_md else ""
            env_manifest_path = _env_md[2] if _env_md else ""
            
            # --- Build EnvironmentConfig ---
            env_config = EnvironmentConfig(
                name=env_name,
                description=env_description,
                rules=env_rules,
                manifest_path=env_manifest_path,
                version=env_version,
                enable_evolving=env_enable_evolving,
                permission_mode=env_permission_mode,
                actions=actions,
                cls=env_cls,
                config=env_config_dict or {},
                instance=env_instance,
                metadata=env_metadata,
                code=env_code
            )
            permission_manager.register(
                entity_name=env_name,
                mode=PermissionMode(env_permission_mode),
            )
            
            # --- Persist current config and history ---
            self._environment_configs[env_name] = env_config
            
            # Store in dict-based history (for quick lookup by version)
            if env_name not in self._environment_history_versions:
                self._environment_history_versions[env_name] = {}
            self._environment_history_versions[env_name][env_config.version] = env_config
            
            # Register version in version manager
            await version_manager.register_version("environment", env_name, env_config.version)
            
            # Persist to JSON
            
            logger.info(f"| 📝 Registered environment config: {env_name}: {env_config.version}")
            return env_config
        
        except Exception as e:
            logger.error(f"| ❌ Failed to register environment: {e}")
            raise
        
    async def get(self, env_name: str, ctx=None) -> Optional[Environment]:
        """Get environment instance by name
        
        Args:
            env_name: Environment name
            
        Returns:
            Environment: Environment instance or None if not found
        """
        env_config = self._environment_configs.get(env_name)
        if env_config:
            return await self.bound(env_config, ctx) if ctx is not None else env_config.instance
        return None
    
    async def get_info(self, env_name: str) -> Optional[EnvironmentConfig]:
        """Get environment configuration by name
        
        Args:
            env_name: Environment name
            
        Returns:
            EnvironmentConfig: Environment configuration or None if not found
        """
        return self._environment_configs.get(env_name)
        
    async def get_state(self, env_name: str, ctx: EnvironmentContext = None, **kwargs) -> Optional[Dict[str, Any]]:
        """Get the state of an environment

        Args:
            env_name: Environment name
            ctx: Environment context
        Returns:
            Optional[Dict[str, Any]]: State of the environment or None if not found
        """

        from agentevolver.runtime.invocation import runtime
        ctx = EnvironmentContext.from_context(ctx)
        info = await self.get_info(env_name)
        if info is None:
            raise ValueError(f"Environment {env_name!r} not found")
        async def observe():
            from agentevolver.runtime.invocation import call_function
            value = await self.bound(info, ctx)
            return await call_function(value.get_state, ctx=ctx, **kwargs)
        return await runtime().invoke("environment", env_name + ":state", observe,
            ctx=ctx, version=info.version, claims=info.instance.resource_claims(ctx, {}, "get_state"))

    async def list(self) -> List[str]:
        """Get list of registered environments
        
        Args:
            include_disabled: Whether to include disabled environments (not used for environments, kept for compatibility)
            
        Returns:
            List[str]: List of registered environment names
        """
        return [name for name in self._environment_configs.keys()]
    
    
    async def update(self, 
                     env_cls: Type[Environment],
                     env_config_dict: Optional[Dict[str, Any]] = None,
                     new_version: Optional[str] = None, 
                     description: Optional[str] = None,
                     code: Optional[str] = None) -> EnvironmentConfig:
        """Update an existing environment with new configuration and create a new version
        
        Args:
            env_cls: New environment class with updated implementation
            env_config_dict: Configuration dict for environment initialization
                   If None, will try to get from global config
            new_version: New version string. If None, auto-increments from current version.
            description: Description for this version update
            code: Optional source code string. If provided, uses this instead of extracting from env_cls.
                  This is useful when env_cls is dynamically created from code string.
            
        Returns:
            EnvironmentConfig: Updated environment configuration
        """
        try:
            if env_config_dict is None:
                # Fallback to global config by class name
                env_config_key = inflection.underscore(env_cls.__name__)
                env_config_dict = getattr(config, env_config_key, {}) if hasattr(config, env_config_key) else {}
            
            # Instantiate environment immediately (update is a runtime operation)
            try:
                env_instance = env_cls(**env_config_dict)
            except Exception as e:
                logger.error(f"| ❌ Failed to create environment instance for {env_cls.__name__}: {e}")
                raise ValueError(f"Failed to instantiate environment {env_cls.__name__} with provided config: {e}")
            
            env_name = env_instance.name
            
            # Check if environment exists
            original_config = self._environment_configs.get(env_name)
            if original_config is None:
                raise ValueError(f"Environment {env_name} not found. Use register() to register a new environment.")
            
            env_description = env_instance.description
            env_metadata = getattr(env_instance, 'metadata', {})
            env_enable_evolving = env_config_dict.get("enable_evolving", getattr(env_instance, 'enable_evolving', False)) if env_config_dict and "enable_evolving" in env_config_dict else getattr(env_instance, 'enable_evolving', False)
            
            # Determine new version from version_manager
            if new_version is None:
                # Get current version from version_manager and generate next patch version
                new_version = await version_manager.generate_next_version("environment", env_name, "patch")
            
            # Get environment code - use provided code if available (for dynamically created classes)
            if code is not None:
                env_code = code
            else:
                env_code = dynamic_manager.get_full_module_source(env_cls)
            
            # Build actions from environment class (same as register)
            actions = {}
            for attr_name in dir(env_cls):
                attr = getattr(env_cls, attr_name)
                if hasattr(attr, '_action_name'):
                    action_name = getattr(attr, '_action_name')
                    action_description = getattr(attr, '_action_description', '')
                    action_function = getattr(attr, '_action_function', None)
                    action_metadata = getattr(attr, '_action_metadata', {})
                    
                    action_version = await version_manager.get_version("action", action_name)
                    
                    action_code = dynamic_manager.get_source_code(attr)
                    if not action_code:
                        logger.warning(f"| ⚠️ Action {action_name} is dynamic but source code cannot be extracted")
                    
                    action_parameters = dynamic_manager.get_parameters(action_function)
                    action_function_calling = dynamic_manager.build_function_calling(action_name, action_description, action_parameters)
                    action_text = dynamic_manager.build_text_representation(action_name, action_description, action_parameters)
                    action_args_schema = dynamic_manager.build_args_schema(action_name, action_parameters)
                    
                    action_config = ActionConfig(
                        env_name=env_name,
                        name=action_name,
                        description=action_description,
                        function=action_function,
                        metadata=action_metadata,
                        version=action_version,
                        code=action_code,
                        function_calling=action_function_calling,
                        text=action_text,
                        args_schema=action_args_schema,
                    )
                    
                    actions[action_name] = action_config
            
            # Rules from the ENVIRONMENT.md beside the class (no code-generated get_rules)
            _env_md = self._load_environment_md(type(env_instance))
            env_rules = _env_md[1] if _env_md else ""
            env_manifest_path = _env_md[2] if _env_md else ""
            
            # --- Build EnvironmentConfig ---
            updated_config = EnvironmentConfig(
                name=env_name,  # Keep same name
                description=env_description,
                rules=env_rules,
                manifest_path=env_manifest_path,
                version=new_version,
                enable_evolving=env_enable_evolving,
                actions=actions,
                cls=env_cls,
                config=env_config_dict or {},
                instance=env_instance,
                metadata=env_metadata,
                code=env_code
            )
            
            # Update the environment config (replaces current version)
            self._environment_configs[env_name] = updated_config
            
            # Store in version history
            if env_name not in self._environment_history_versions:
                self._environment_history_versions[env_name] = {}
            self._environment_history_versions[env_name][updated_config.version] = updated_config
            
            # Register new version record to version manager
            await version_manager.register_version(
                "environment", 
                env_name, 
                new_version,
                description=description or f"Updated from {original_config.version}"
            )
            
            # Persist to JSON
            
            logger.info(f"| 🔄 Updated environment {env_name} from v{original_config.version} to v{new_version}")
            return updated_config
        
        except Exception as e:
            logger.error(f"| ❌ Failed to update environment: {e}")
            raise
    
    async def copy(self, 
                  env_name: str,
                  new_name: Optional[str] = None, 
                  new_version: Optional[str] = None, 
                  new_config: Optional[Dict[str, Any]] = None) -> EnvironmentConfig:
        """Copy an existing environment configuration
        
        Args:
            env_name: Name of the environment to copy
            new_name: New name for the copied environment. If None, uses original name.
            new_version: New version for the copied environment. If None, increments version.
            new_config: New configuration dict for the copied environment. If None, uses original config.
            
        Returns:
            EnvironmentConfig: New environment configuration
        """
        try:
            original_config = self._environment_configs.get(env_name)
            if original_config is None:
                raise ValueError(f"Environment {env_name} not found")
            
            if original_config.cls is None:
                raise ValueError(f"Cannot copy environment {env_name}: no class provided")
            
            # Determine new name
            if new_name is None:
                new_name = env_name
            
            # Prepare config dict (merge original config with new config)
            env_config_dict = original_config.config.copy() if original_config.config else {}
            if new_config:
                # Merge new config into original config
                env_config_dict.update(new_config)
            
            # Instantiate environment instance (copy is a runtime operation)
            try:
                env_instance = original_config.cls(**env_config_dict)
            except Exception as e:
                logger.error(f"| ❌ Failed to create environment instance for {original_config.cls.__name__}: {e}")
                raise ValueError(f"Failed to instantiate environment {original_config.cls.__name__} with provided config: {e}")
            
            # Apply name override if provided (after instantiation)
            if new_name != env_name:
                env_instance.name = new_name
            
            env_description = env_instance.description
            env_metadata = getattr(env_instance, 'metadata', {})
            env_enable_evolving = env_config_dict.get("enable_evolving", getattr(env_instance, 'enable_evolving', False)) if env_config_dict and "enable_evolving" in env_config_dict else getattr(env_instance, 'enable_evolving', False)
            
            # Determine new version from version_manager
            if new_version is None:
                if new_name == env_name:
                    # If copying with same name, get next version from version_manager
                    new_version = await version_manager.generate_next_version("environment", new_name, "patch")
                else:
                    # If copying with different name, get or generate version for new name
                    new_version = await version_manager.get_version("environment", new_name)
            
            # Get environment code
            env_code = dynamic_manager.get_full_module_source(original_config.cls)
            
            # Build actions from environment class (same as register)
            actions = {}
            for attr_name in dir(original_config.cls):
                attr = getattr(original_config.cls, attr_name)
                if hasattr(attr, '_action_name'):
                    action_name = getattr(attr, '_action_name')
                    action_description = getattr(attr, '_action_description', '')
                    action_function = getattr(attr, '_action_function', None)
                    action_metadata = getattr(attr, '_action_metadata', {})
                    
                    action_version = await version_manager.get_version("action", action_name)
                    
                    action_code = dynamic_manager.get_source_code(attr)
                    if not action_code:
                        logger.warning(f"| ⚠️ Action {action_name} is dynamic but source code cannot be extracted")
                    
                    action_parameters = dynamic_manager.get_parameters(action_function)
                    action_function_calling = dynamic_manager.build_function_calling(action_name, action_description, action_parameters)
                    action_text = dynamic_manager.build_text_representation(action_name, action_description, action_parameters)
                    action_args_schema = dynamic_manager.build_args_schema(action_name, action_parameters)
                    
                    action_config = ActionConfig(
                        env_name=new_name,
                        name=action_name,
                        description=action_description,
                        function=action_function,
                        metadata=action_metadata,
                        version=action_version,
                        code=action_code,
                        function_calling=action_function_calling,
                        text=action_text,
                        args_schema=action_args_schema,
                    )
                    
                    actions[action_name] = action_config
            
            # Rules from the ENVIRONMENT.md beside the class (no code-generated get_rules)
            _env_md = self._load_environment_md(type(env_instance))
            env_rules = _env_md[1] if _env_md else ""
            env_manifest_path = _env_md[2] if _env_md else ""
            
            # --- Build EnvironmentConfig ---
            copied_config = EnvironmentConfig(
                name=new_name,
                description=env_description,
                rules=env_rules,
                manifest_path=env_manifest_path,
                version=new_version,
                enable_evolving=env_enable_evolving,
                actions=actions,
                cls=original_config.cls,
                config=env_config_dict,
                instance=env_instance,
                metadata=env_metadata,
                code=env_code
            )
            
            # Register new environment
            self._environment_configs[new_name] = copied_config
            
            # Store in version history
            if new_name not in self._environment_history_versions:
                self._environment_history_versions[new_name] = {}
            self._environment_history_versions[new_name][new_version] = copied_config
            
            # Register version record to version manager
            await version_manager.register_version(
                "environment", 
                new_name, 
                new_version,
                description=f"Copied from {env_name}@{original_config.version}"
            )
            
            # Persist to JSON
            
            logger.info(f"| 📋 Copied environment {env_name}@{original_config.version} to {new_name}@{new_version}")
            return copied_config
        
        except Exception as e:
            logger.error(f"| ❌ Failed to copy environment: {e}")
            raise
    
    async def unregister(self, env_name: str) -> bool:
        """Unregister an environment
        
        Args:
            env_name: Name of the environment to unregister
            
        Returns:
            True if unregistered successfully, False otherwise
        """
        if env_name not in self._environment_configs:
            logger.warning(f"| ⚠️ Environment {env_name} not found")
            return False
        
        env_config = self._environment_configs[env_name]
        
        # Remove from configs
        del self._environment_configs[env_name]

        # Persist to JSON after unregister
        # Save contract to file
        
        logger.info(f"| 🗑️ Unregistered environment {env_name}@{env_config.version}")
        return True
    
    async def restore(self, env_name: str, version: str, auto_initialize: bool = True) -> Optional[EnvironmentConfig]:
        """Restore a specific version of an environment from history
        
        Args:
            env_name: Name of the environment
            version: Version string to restore
            auto_initialize: Whether to automatically initialize the restored environment
            
        Returns:
            EnvironmentConfig of the restored version, or None if not found
        """
        # Look up version from dict-based history (O(1) lookup)
        version_config = None
        if env_name in self._environment_history_versions:
            version_config = self._environment_history_versions[env_name].get(version)
        
        if version_config is None:
            logger.warning(f"| ⚠️ Version {version} not found for environment {env_name}")
            return None
        
        # Keep live classes, callables and argument schemas. model_dump() uses the
        # display serializers and cannot be round-tripped into a runtime config.
        # Restore operates on this copy, leaving the history config intact.
        restored_config = version_config.model_copy()
        
        # Set as current active config
        self._environment_configs[env_name] = restored_config
        
        # Update version manager current version
        version_history = await version_manager.get_version_history("environment", env_name)
        if version_history:
            # Check if version exists in version history, if not register it
            if version not in version_history.versions:
                await version_manager.register_version("environment", env_name, version)
            version_history.current_version = version
        else:
            # If version history doesn't exist, register the version first
            await version_manager.register_version("environment", env_name, version)
        
        # Initialize if requested
        if auto_initialize and restored_config.cls is not None:
            await self.build(restored_config)
        
        # Persist to JSON (current_version changes)
        
        logger.info(f"| 🔄 Restored environment {env_name} to version {version}")
        return restored_config
    
    async def _ensure_sandbox_server(self, env_configs: Dict[str, EnvironmentConfig], force: bool = False) -> None:
        """Start opensandbox-server if any registered environment requires it."""
        needs_sandbox = force or any(
            cfg.config and cfg.config.get("use_sandbox", False)
            for cfg in env_configs.values()
        )
        if not needs_sandbox:
            return
        logger.info("| 🔍 Sandbox-based environment detected — ensuring opensandbox-server is running")
        await self._sandbox_server.ensure_running()

    async def cleanup(self):
        """Join calls before closing shared backends; retain failed resources for retry."""
        from agentevolver.runtime.invocation import runtime
        await runtime().release(module="environment")
        errors = []
        for name, info in self._environment_configs.items():
            value = info.instance
            if value and (value.managed_sessions or value.state_scope == "shared") and hasattr(value, "cleanup"):
                try:
                    await value.cleanup()
                except Exception as error:
                    errors.append(error)
        if errors:
            raise ExceptionGroup("Environment backend cleanup failed", errors)
        await self._sandbox_server.shutdown()
        for name in self._environment_configs:
            permission_manager.unregister(name)
        self._environment_configs.clear()
        self._environment_history_versions.clear()
        logger.info("| 🧹 Environment context manager cleaned up")

    async def __call__(self,
                       name: str, 
                       action: str, 
                       input: Dict[str, Any], 
                       ctx: EnvironmentContext = None,
                       **kwargs) -> Response:
        """Call an environment action

        Args:
            name (str): Name of the environment
            action (str): Name of the action
            input (Dict[str, Any]): Input for the action
            ctx (EnvironmentContext): Environment context
            
        Returns:
            Response: the action's outcome, in the shape every capability returns
        """
        ctx = EnvironmentContext.from_context(ctx)
        ctx.action = action
        env_info = await self.get_info(name)
        if env_info is None:
            raise ValueError(f"Environment {name!r} not found")
        action_info = env_info.actions.get(action)
        if action_info is None:
            return Response(
                type=ResponseType.ENVIRONMENT,
                success=False,
                message=f"Action {action!r} not found in environment {name!r}.",
            )
        call_input = dict(input or {})
        preflight_error = None
        try:
            artifact_claims = self._artifact_claims(env_info, ctx, call_input, action)
        except (TypeError, ValueError) as error:
            from agentevolver.tool.execution import ToolErrorCode
            preflight_error = (ToolErrorCode.INVALID_ARGUMENTS, str(error))
            artifact_claims = ()
        execution = ToolExecution.create(
            name=f"{name}__{action}",
            version=env_info.version,
            arguments=call_input,
            ctx=ctx,
        )
        call_input = execution.arguments
        ctx.input = dict(call_input)
        metadata = dict(action_info.metadata or {})
        effect = EffectContract.from_annotations(metadata)

        def effect_guard(_execution):
            op = metadata.get("permission_op")
            target_arg = metadata.get("permission_target")
            if op and target_arg:
                target = str(call_input.get(str(target_arg), "") or "")
                checked = permission_manager.check(
                    name,
                    PermissionRequest(op=Operation(str(op)), target=target),
                )
                if not checked.allowed:
                    return ToolPolicyDecision.deny(
                        checked.reason or "Environment operation denied."
                    )
                if checked.requires_approval:
                    return ToolPolicyDecision.ask(
                        checked.warning or "Environment operation requires approval."
                    )
            decision = effect.policy_decision(
                mode=env_info.permission_mode,
                label=f"Environment action {name}__{action}",
            )
            if not decision.allowed:
                return ToolPolicyDecision.deny(decision.reason or "Environment action denied.")
            if decision.requires_approval:
                return ToolPolicyDecision.ask(
                    decision.warning or "Environment action requires approval."
                )
            return None

        async def checkpoint_effect() -> None:
            if effect.read_only is True:
                return
            from agentevolver.trace.integrity import TraceDurabilityBoundary, ensure_trace_durable

            await ensure_trace_durable(
                execution.session_id,
                TraceDurabilityBoundary.EXTERNAL_EFFECT,
                ctx=ctx,
                metadata={"environment": name, "action": action},
            )

        async def invoke() -> Response:
            result = await self._invoke_config(env_info, action, call_input, ctx, **kwargs)
            return self._normalize_response(name, action, result)

        return await self._execution_pipeline.execute(
            execution,
            invoke,
            timeout=None,
            runtime_options={
                "ctx": ctx,
                "claims": lambda: (*env_info.instance.resource_claims(ctx, call_input, action), *artifact_claims),
                "max_concurrency": (None if metadata.get("capacity_exempt") is True
                                    else env_info.instance.max_concurrency),
                "limit_key": ("environment", env_info.instance.concurrency_group or name),
            },
            call_guards=[effect_guard],
            preflight_error=preflight_error,
            before_invoke=checkpoint_effect,
        )

    @staticmethod
    def _normalize_response(name, action, result):
        from agentevolver.environment.server import EnvironmentManagerServer
        return EnvironmentManagerServer._normalize_response(name, action, result)

    @staticmethod
    def _artifact_claims(info, ctx, arguments, action):
        """Normalize declared paths before permission checks and runtime admission."""
        from agentevolver.runtime.invocation import ResourceClaim
        from agentevolver.session import resolve_workspace_root
        claims = []
        metadata = dict(info.actions[action].metadata or {})
        for key, shared in (("read_paths", True), ("write_paths", False)):
            for argument in metadata.get(key, ()):
                value = arguments.get(argument)
                if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
                    raise ValueError(f"{action} requires a nonempty path in {argument!r}")
                path = Path(value).expanduser()
                if not path.is_absolute():
                    root = resolve_workspace_root(ctx)
                    if not root:
                        raise ValueError(f"Relative path {value!r} requires a workspace context")
                    path = Path(root) / path
                # The method and the scheduler must refer to exactly the same file.
                arguments[argument] = str(path.resolve())
                claims.append(ResourceClaim.path(path, shared=shared))
        return tuple(claims)

    async def live_view(self, name, ctx):
        from agentevolver.runtime.invocation import runtime
        info = await self.get_info(name)
        if info is None:
            return None
        if getattr(info.cls, "live_view", None) is Environment.live_view:
            # Public dispatch probes views after every action. A numerical evaluator
            # with no view must not initialize a second, immediately discarded trial.
            return None
        async def observe():
            from agentevolver.runtime.invocation import call_function
            value = await self.bound(info, ctx)
            return await call_function(value.live_view, ctx)
        return await runtime().invoke("environment", name + ":view", observe,
            ctx=ctx, version=info.version, claims=info.instance.resource_claims(ctx, {}, "live_view"))

    async def bound(self, info, ctx):
        from agentevolver.runtime.invocation import runtime, owner_id, call_function
        prototype = info.instance
        owner = owner_id(ctx)
        async def create():
            if prototype.managed_sessions or prototype.state_scope == "shared":
                return prototype
            value = info.cls(**(info.config or {}))
            value.name = info.name
            try:
                if hasattr(value, "initialize"):
                    await call_function(value.initialize)
            except BaseException:
                if hasattr(value, "cleanup"):
                    try:
                        await call_function(value.cleanup)
                    except BaseException:
                        # Failed initialization never becomes a usable binding, but its
                        # partially acquired backend must remain available for cleanup.
                        runtime().own("environment", f"{info.name}:initialization:{id(value)}",
                                      owner, value, close)
                        raise
                raise
            return value
        async def close(value):
            if value.managed_sessions:
                if hasattr(value, "close_session"):
                    await call_function(value.close_session, owner)
            elif value.state_scope != "shared" and hasattr(value, "cleanup"):
                await call_function(value.cleanup)
        return await runtime().bind("environment", info.name, info.version, owner, create, close,
                                    scope="call" if prototype.state_scope == "call" else "owner")

    async def _invoke_config(self, info, action, input, ctx, **kwargs):
        from agentevolver.runtime.invocation import call_function
        value = await self.bound(info, ctx)
        call = value.actions.get(action)
        if call is None:
            raise ValueError(f"Action {action!r} is not available in {info.name}")
        args = dict(input or {})
        args.pop("ctx", None)
        function = call.function
        positional = () if hasattr(function, "__self__") else (value,)
        return await call_function(function, *positional, **args, ctx=ctx)
