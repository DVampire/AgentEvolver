"""ECP Server

Server implementation for the Environment Context Protocol with lazy loading support.
"""
import copy
import json
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.capability import CapabilitySchema, SchemaSource, roster, roster_card
from agentevolver.config import config
from agentevolver.environment.context import EnvironmentContextManager
from agentevolver.environment.types import Environment, EnvironmentConfig, EnvironmentContext
from agentevolver.logger import logger
from agentevolver.paths import P, path_manager
from agentevolver.response.types import Response, ResponseType
from agentevolver.utils import assemble_workspace_path


class EnvironmentManagerServer(BaseModel):
    """ECP Server for managing environment registration and execution with lazy loading."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    base_dir: str = Field(default=None, description="The base directory to use for the environments")
    
    def __init__(self, base_dir: Optional[str] = None, **kwargs):
        """Initialize the ECP Server."""
        super().__init__(**kwargs)
        # Created lazily: config may not be loaded at import time. initialize()
        # reconfigures it with the proper base_dir.
        self.environment_context_manager: Optional[EnvironmentContextManager] = None
        self._registered_configs: Dict[str, EnvironmentConfig] = {}  # env_name -> EnvironmentConfig
        # (session_id, env_name, owner_id) -> last announced live-view URL, to dedupe announcements.
        self._announced_views: Dict[tuple, str] = {}

    def _ensure_context_manager(self) -> EnvironmentContextManager:
        """Lazily create the context manager so methods work before initialize().

        Every other capability manager has this. Without it, asking this one
        anything before ``initialize()`` raised ``AttributeError`` — which reads
        as a missing feature rather than as "no environments are loaded yet", and
        is the one manager where a roster lookup could take a caller down.
        """
        if self.environment_context_manager is None:
            self.environment_context_manager = EnvironmentContextManager()
        return self.environment_context_manager

    async def initialize(self, env_names: Optional[List[str]] = None):
        """Initialize environments by names using environment context manager with concurrent support.
        
        Args:
            env_names: List of environment names to initialize. If None, initialize all registered environments.
        """

        base_root = config.log_root if hasattr(config, "log_root") and config.get("log_root") else config.workspace_root
        self.base_dir = assemble_workspace_path(path_manager.under(base_root, P.LOG_MODULE, module="environment"))
        logger.info(f"| 📁 ECP Server base directory: {self.base_dir}")

        # Re-initialization changes the mounted environment set. Release live browser,
        # SSH and sandbox resources owned by the previous set before replacing it.
        if self.environment_context_manager is not None:
            await self.environment_context_manager.cleanup()

        # Initialize environment context manager
        self.environment_context_manager = EnvironmentContextManager(
            base_dir=self.base_dir,
        )
        await self.environment_context_manager.initialize(env_names=env_names)
        
        logger.info("| ✅ Environments initialization completed")
        
    def action(self, 
               name: str = None, 
               description: str = "",
               metadata: Optional[Dict[str, Any]] = None,
               *,
               read_only: Optional[bool] = None,
               destructive: Optional[bool] = None,
               idempotent: Optional[bool] = None,
               open_world: Optional[bool] = None,
               parallel_safe: Optional[bool] = None,
               capacity_exempt: Optional[bool] = None,
               read_paths: tuple[str, ...] = (),
               write_paths: tuple[str, ...] = (),
               permission_op: Optional[str] = None,
               permission_target: Optional[str] = None):
        """Decorator to register an action (tool) for an environment
        
        Actions will be registered to the environment instance's actions dictionary during instantiation.
        
        Args:
            name: Action name (defaults to function name)
            description: Action description
            metadata: Action metadata
            parallel_safe: Allow independent batch dispatch; resource claims still apply.
            capacity_exempt: Short control/observation call bypasses worker capacity only.
            read_paths: Argument names containing input paths (shared access).
            write_paths: Argument names containing output paths (exclusive access).
        """
        def decorator(func: Callable):
            action_name = name or func.__name__
            
            func._action_name = action_name
            func._action_description = description
            func._action_function = func
            effect = dict(metadata or {})
            if read_only is not None:
                effect.setdefault("read_only", read_only)
            if destructive is not None:
                effect.setdefault("destructive", destructive)
            if open_world is not None:
                effect.setdefault("open_world", open_world)
            if idempotent is not None:
                effect.setdefault("idempotent", idempotent)
            if parallel_safe is not None:
                effect.setdefault("parallel_safe", parallel_safe)
            if capacity_exempt is not None:
                effect.setdefault("capacity_exempt", capacity_exempt)
            for key, paths in (("read_paths", read_paths), ("write_paths", write_paths)):
                if isinstance(paths, str) or any(not isinstance(p, str) or not p for p in paths):
                    raise ValueError(f"{key} must contain argument names")
                if paths:
                    effect[key] = tuple(paths)
            if permission_op:
                effect["permission_op"] = permission_op
            if permission_target:
                effect["permission_target"] = permission_target
            func._action_metadata = effect
            
            return func
        return decorator
    
    async def register(self, 
                       env_cls: Type[Environment],
                       env_config_dict: Optional[Dict[str, Any]] = None,
                       override: bool = False,
                       version: Optional[str] = None) -> EnvironmentConfig:
        """Register an environment class asynchronously.
        
        Args:
            env_cls: Environment class to register
            env_config_dict: Configuration dict for environment initialization
            override: Whether to override existing registration
            version: Optional version string
            
        Returns:
            EnvironmentConfig: Environment configuration
        """
        if not hasattr(self, "environment_context_manager"):
            await self.initialize(env_names=[])
        env_config = await self._ensure_context_manager().register(
            env_cls,
            env_config_dict=env_config_dict, 
            override=override,
            version=version
        )
        self._registered_configs[env_config.name] = env_config
        return env_config
    
    async def list(self) -> List[str]:
        """List all registered environments
        
        Returns:
            List[str]: List of environment names
        """
        if not hasattr(self, "environment_context_manager"):
            return []
        return await self._ensure_context_manager().list()

    async def function_callings(
        self, allowlist: Optional[List[str]] = None, types: Optional[List[str]] = None
    ) -> List[Tuple[Dict[str, Any], Tuple[str, str, str]]]:
        """Expose selected environment actions as native tool-calling schemas.

        ``types`` is accepted for a uniform manager interface — environments have
        no type filter — so a caller can project every capability type in one loop
        instead of remembering which manager takes which arguments.
        """
        names = allowlist if allowlist is not None else await self.list()
        out: List[Tuple[Dict[str, Any], Tuple[str, str, str]]] = []
        for env_name in names:
            info = await self.get_info(env_name)
            if info is None:
                continue
            for action_name in (getattr(info, "actions", {}) or {}):
                fc = await self.get_schema(env_name, action=action_name, format="json")
                if fc:
                    out.append((fc, ("environment", env_name, action_name)))
        return out

    async def get_schema(self, name: str, action: Optional[str] = None, format: str = "json"):
        """Return one Environment action contract from declared/inferred metadata."""
        info = await self.get_info(name)
        actions = (getattr(info, "actions", {}) or {}) if info is not None else {}
        item = actions.get(action) if action else None
        if item is None:
            return None
        function_calling = getattr(item, "function_calling", None) or {}
        function = function_calling.get("function", {}) if isinstance(function_calling, dict) else {}
        parameters = function.get("parameters") if isinstance(function, dict) else None
        source = SchemaSource.DECLARED
        if not isinstance(parameters, dict):
            args_schema = getattr(item, "args_schema", None)
            if args_schema is not None and hasattr(args_schema, "model_json_schema"):
                parameters = args_schema.model_json_schema()
                source = SchemaSource.INFERRED
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "additionalProperties": True}
            source = SchemaSource.LEGACY_FALLBACK
        else:
            # ``ctx`` is runtime-owned injection state, not model input.  Environment
            # action methods declare it so the manager can supply the active session,
            # but exposing it in the native tool schema lets strict providers emit a
            # second value and makes ``action(**input, ctx=ctx)`` fail before the
            # action runs.  Project a defensive copy so persisted action metadata is
            # unchanged while every provider sees only user-controlled arguments.
            parameters = copy.deepcopy(parameters)
            properties = parameters.get("properties")
            if isinstance(properties, dict):
                properties.pop("ctx", None)
            required = parameters.get("required")
            if isinstance(required, list):
                parameters["required"] = [item for item in required if item != "ctx"]
        return CapabilitySchema(
            name=f"{name}__{action}",
            description=getattr(item, "description", "") or f"{name}: {action}",
            parameters=parameters,
            strict=parameters.get("additionalProperties") is False,
            source=source,
        ).render(format)
    
    
    async def get(self, env_name: str, ctx=None) -> Optional[Environment]:
        """Get environment instance by name
        
        Args:
            env_name: Environment name
            
        Returns:
            Environment: Environment instance or None if not found
        """
        return await self._ensure_context_manager().get(env_name, ctx=ctx)
    
    async def live_view(self, name, ctx):
        return await self._ensure_context_manager().live_view(name, EnvironmentContext.from_context(ctx))

    async def get_info(self, env_name: str) -> Optional[EnvironmentConfig]:
        """Get environment configuration by name
        
        Args:
            env_name: Environment name
            
        Returns:
            EnvironmentConfig: Environment configuration or None if not found
        """
        return await self._ensure_context_manager().get_info(env_name)
    
    async def get_state(self, env_name: str, ctx: EnvironmentContext = None, **kwargs) -> Optional[Dict[str, Any]]:
        """Get the state of an environment
        
        Args:
            env_name: Environment name
            ctx: Environment context
            
        Returns:
            Optional[Dict[str, Any]]: State of the environment or None if not found
        """
        return await self._ensure_context_manager().get_state(env_name, ctx, **kwargs)

    async def get_instruction(self, allowlist: Optional[List[str]] = None,
                              types: Optional[List[str]] = None,
                              level: str = "brief") -> str:
        """Assemble the environment roster for prompt injection.

        The connector and plugin shape, because an environment is the same thing:
        one container with several separately callable members and one document
        describing it. ``brief`` names ENVIRONMENT.md, ``full`` is ENVIRONMENT.md —
        which is where its rules and its actions' arguments are written for the
        model to read, in one place a human edits and reviews.

        The manager owns the facts, so the agent side is a wrapper rather than
        something that has to know what an environment is made of. Without that, an
        agent that wanted this text had to walk ``info.actions`` itself — which is
        why two agents once inherited a mixin to do it, and why environment context
        reached only those two rather than every agent.

        Args:
            allowlist: Which environments to include. ``None`` = all registered,
                ``[]`` = none, ``[names]`` = only those — the same contract every
                other capability manager uses.
            types: Accepted for a uniform manager interface; environments have no
                type filter.
            level: ``brief`` for the roster, ``full`` to include ENVIRONMENT.md.

        Returns:
            The rendered cards, joined.
        """
        names = allowlist if allowlist is not None else await self.list()
        parts: List[str] = []
        for env_name in names:
            info = await self.get_info(env_name)
            if info is None:
                continue
            # The prose names an action `run`; the schema a model is given calls it
            # `remote_host__run`, because two environments may both have a `run` and the
            # tool namespace is flat. Read from the same actions the schemas are built
            # from rather than rebuilt, and printed where that prose is.
            callable_names = sorted(
                f"{env_name}__{action}" for action in (getattr(info, "actions", {}) or {})
            )
            parts.append(roster_card(
                env_name, getattr(info, "description", "") or "",
                meta=f"v{getattr(info, 'version', '')}",
                manifest_label="ENVIRONMENT.md",
                manifest_path=getattr(info, "manifest_path", "") or "",
                document=getattr(info, "rules", "") or "",
                footer=("Call these by their full names: "
                        + ", ".join(f"`{name}`" for name in callable_names)) if callable_names else "",
                level=level,
            ))
        return roster(parts)

    async def cleanup(self):
        """Cleanup all environments"""
        await self._ensure_context_manager().cleanup()
        self._registered_configs.clear()
    
    async def update(self, 
                     env_cls: Type[Environment],
                     env_config_dict: Optional[Dict[str, Any]] = None,
                     new_version: Optional[str] = None, 
                     description: Optional[str] = None) -> EnvironmentConfig:
        """Update an existing environment with new configuration and create a new version
        
        Args:
            env_cls: New environment class with updated implementation
            env_config_dict: Configuration dict for environment initialization
            new_version: New version string. If None, auto-increments from current version.
            description: Description for this version update
            
        Returns:
            EnvironmentConfig: Updated environment configuration
        """
        env_config = await self._ensure_context_manager().update(
            env_cls, env_config_dict=env_config_dict, new_version=new_version, description=description
        )
        self._registered_configs[env_config.name] = env_config
        return env_config
    
    async def copy(self, 
                  env_name: str,
                  new_name: Optional[str] = None, 
                  new_version: Optional[str] = None, 
                  new_config: Optional[Dict[str, Any]] = None) -> EnvironmentConfig:
        """Copy an existing environment
        
        Args:
            env_name: Name of the environment to copy
            new_name: New name for the copied environment. If None, uses original name.
            new_version: New version for the copied environment. If None, increments version.
            new_config: New configuration dict for the copied environment. If None, uses original config.
            
        Returns:
            EnvironmentConfig: New environment configuration
        """
        env_config = await self._ensure_context_manager().copy(
            env_name, new_name, new_version, new_config
        )
        self._registered_configs[env_config.name] = env_config
        return env_config
    
    async def unregister(self, env_name: str) -> bool:
        """Unregister an environment
        
        Args:
            env_name: Name of the environment to unregister
            
        Returns:
            True if unregistered successfully, False otherwise
        """
        success = await self._ensure_context_manager().unregister(env_name)
        if success and env_name in self._registered_configs:
            del self._registered_configs[env_name]
        return success
    
    async def restore(self, env_name: str, version: str, auto_initialize: bool = True) -> Optional[EnvironmentConfig]:
        """Restore a specific version of an environment from history
        
        Args:
            env_name: Name of the environment
            version: Version string to restore
            auto_initialize: Whether to automatically initialize the restored environment
            
        Returns:
            EnvironmentConfig of the restored version, or None if not found
        """
        env_config = await self._ensure_context_manager().restore(env_name, version, auto_initialize)
        if env_config:
            self._registered_configs[env_config.name] = env_config
        return env_config
    
    async def __call__(self, name, action, input, ctx=None, **kwargs):
        ctx = EnvironmentContext.from_context(ctx)
        manager = self._ensure_context_manager()
        result = await manager(name, action, input, ctx, **kwargs)
        await self._announce_live_view(name, ctx)
        return self._normalize_response(name, action, result)

    @staticmethod
    def _normalize_response(name: str, action: str, result: Any) -> Response:
        """Normalize the convenient environment result shapes at one boundary."""
        # One return type, the same one tool / skill / connector give back. Actions may
        # return a plain dict because that is convenient to write — the SSH helpers are
        # literally `{"success": True, **payload}` — and converting it here is what keeps
        # every caller identical.
        #
        # Both of this boundary's bugs came from not doing it. One caller read
        # `result["message"]` and nothing else, and not one of sixteen SSH actions sets
        # `message`, so the agent saw None from everything it did and re-ran the same
        # actions step after step. Another passed the dict down a chain that takes text,
        # and the run went silent mid-task with no error and no next step.
        if isinstance(result, Response):
            return result
        if not isinstance(result, dict):
            return Response(type=ResponseType.ENVIRONMENT, success=True,
                            message="" if result is None else str(result))

        success = bool(result.get("success", True))
        payload = {k: v for k, v in result.items() if k != "success"}
        message = payload.pop("message", None)
        if message is None:
            # No prose, so the payload IS the answer. Serializing it is the step the
            # message-only reading skipped, and skipping it is what made a directory
            # listing, a file read and a job list all arrive as nothing at all.
            try:
                message = json.dumps(payload, ensure_ascii=False, indent=2, default=str) if payload else ""
            except (TypeError, ValueError):
                message = str(payload)
            if not message:
                message = "(no output)" if success else f"{name}.{action} failed"
        return Response(type=ResponseType.ENVIRONMENT, success=success,
                        message=message, data=payload or None)

    def set_approval_resolver(self, resolver):
        """Install the same one-shot approval channel used by tools/connectors."""
        return self._ensure_context_manager()._execution_pipeline.set_approval_resolver(resolver)

    async def _announce_live_view(self, name: str, ctx: EnvironmentContext) -> None:
        """Announce this environment's live-view endpoint on change (idempotent)."""
        try:
            # The context manager checks whether a view exists and binds it inside
            # an invocation. A direct get() here is outside that lifetime and fails
            # for call-scoped numerical evaluators after their action has finished.
            view = await self._ensure_context_manager().live_view(name, ctx)
            if view is None:
                return
            view.session_id = view.session_id or getattr(ctx, "id", "") or ""
            view.env_name = view.env_name or name
            from agentevolver.runtime.invocation import owner_id
            view.owner_id = owner_id(ctx)
            key = (view.session_id, name, view.owner_id)
            if self._announced_views.get(key) == view.url:
                return  # same endpoint already announced — don't spam the bus
            self._announced_views[key] = view.url
            from agentevolver.environment.stream import environment_stream
            await environment_stream.emit(view)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"| ⚠️ live_view announce failed for '{name}': {exc}")


# Global EnvironmentManager server instance
environment_manager = EnvironmentManagerServer()
