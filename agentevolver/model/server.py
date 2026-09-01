"""Model manager server — global singleton, mirrors agent_manager pattern."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.message.types import Message
from agentevolver.model.context import ModelContextManager
from agentevolver.model.types import ModelContext, ModelConfig
from agentevolver.response.types import Response, ResponseType


class ModelManagerServer(BaseModel):
    """Global model manager — thin facade over ModelContextManager."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_context_manager: ModelContextManager = Field(default_factory=ModelContextManager)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize all provider model registrations."""
        await self.model_context_manager.initialize()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register_model(self, config: ModelConfig) -> None:
        """Register a custom model configuration at runtime."""
        await self.model_context_manager.register_model(config)

    async def unregister_model(self, model_name: str) -> bool:
        """Remove a custom runtime model configuration."""
        return await self.model_context_manager.unregister_model(model_name)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_model_config(self, model: str) -> Optional[ModelConfig]:
        """Return the ModelConfig for a registered model name."""
        return self.model_context_manager.get_model_config(model)

    def list(self) -> List[str]:
        """List all registered model names."""
        return self.model_context_manager.list()

    async def alist(self) -> List[str]:
        return self.model_context_manager.list()

    async def aget_model_config(self, model: str) -> Optional[ModelConfig]:
        return self.model_context_manager.get_model_config(model)

    def resolve_runtime_features(
        self, model: str, requested: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return native/fallback modes for one exact registered model route."""
        return self.model_context_manager.resolve_runtime_features(model, requested)

    async def compact_history(
        self,
        name: str,
        messages: List[Message],
        *,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        step_number: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Delegate provider-native compaction through the public model facade."""
        return await self.model_context_manager.compact_history(
            name,
            messages,
            session_id=session_id,
            task_id=task_id,
            agent_name=agent_name,
            step_number=step_number,
            max_output_tokens=max_output_tokens,
        )

    async def retrieve_background(
        self, name: str, response_id: str, **trace: Any,
    ) -> Response:
        return await self.model_context_manager.retrieve_background(
            name, response_id, **trace,
        )

    async def create_background(
        self,
        name: str,
        input: Dict[str, Any],
        ctx: ModelContext = None,
        **kwargs: Any,
    ) -> Response:
        payload = {**input, "background": True, "store": True}
        return await self.model_context_manager(
            name=name, input=payload, ctx=ctx, **kwargs,
        )

    async def cancel_background(
        self, name: str, response_id: str, **trace: Any,
    ) -> Response:
        return await self.model_context_manager.cancel_background(
            name, response_id, **trace,
        )

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    def resolve_role(self, role: Optional[str]) -> Optional[str]:
        """Resolve a model name from a named role via ``config.model_roles``.

        A missing/unknown role falls back to the ``main`` role, then to
        ``config.model_name``. Returns None if nothing is configured.
        """
        try:
            from agentevolver.config import config
            roles = getattr(config, "model_roles", None) or {}
        except Exception:
            roles = {}
        if isinstance(roles, dict):
            if role and roles.get(role):
                return roles[role]
            if roles.get("main"):
                return roles["main"]
        try:
            from agentevolver.config import config
            return getattr(config, "model_name", None)
        except Exception:
            return None

    async def __call__(
        self,
        name: Optional[str] = None,
        input: Optional[Dict[str, Any]] = None,
        ctx: ModelContext = None,
        role: Optional[str] = None,
        **kwargs: Any,
    ) -> Response:
        """Invoke a model by name, or by named role.

        Args:
            name:  Registered model name (e.g. "openrouter/gemini-3-flash-preview").
                   If omitted, resolved from ``role`` via ``config.model_roles``.
            input: Call payload — keys: messages (required), tools, response_format,
                   stream, plugins, max_retries, caller.
            ctx:   Optional ModelContext carrying caller tag, retry count, timeout, etc.
            role:  Optional model role (main | judge | summarize | smoke | …). Used only
                   when ``name`` is not given; a missing role falls back to ``main``.
        """
        input = input or {}
        if not name:
            name = self.resolve_role(role)
        if not name:
            return Response(type=ResponseType.LLM, success=False,
                            message="No model resolved: pass `name=` or configure `model_roles` for `role=`.")
        if not input.get("messages"):
            return Response(type=ResponseType.LLM, success=False, message="'messages' is required in input and must be non-empty.")
        return await self.model_context_manager(name=name, input=input, ctx=ctx, **kwargs)

    async def stream(
        self,
        name: Optional[str] = None,
        input: Optional[Dict[str, Any]] = None,
        ctx: ModelContext = None,
        role: Optional[str] = None,
        **kwargs: Any,
    ):
        """Stream a model invocation, yielding canonical stream events.

        Same name/role resolution as ``__call__``. Consume with an ``async for``;
        use ``agentevolver.model.types.accumulate_stream`` to fold events into a final
        ``{text, thinking, tool_calls, stop_reason, usage}`` when you want the
        buffered result after streaming.
        """
        input = input or {}
        if not name:
            name = self.resolve_role(role)
        async for ev in self.model_context_manager.stream(name=name, input=input, ctx=ctx, **kwargs):
            yield ev


# Global singleton
model_manager = ModelManagerServer()
