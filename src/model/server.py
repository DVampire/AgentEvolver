"""Model manager server — global singleton, mirrors agent_manager pattern."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.model.context import ModelContextManager
from src.model.types import ModelContext, ModelConfig, LLMResponse


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

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    async def __call__(
        self,
        name: str,
        input: Dict[str, Any],
        ctx: ModelContext = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Invoke a model by name.

        Args:
            name:  Registered model name (e.g. "openrouter/gemini-3-flash-preview").
            input: Call payload — keys: messages (required), tools, response_format,
                   stream, plugins, max_retries, caller.
            ctx:   Optional ModelContext carrying caller tag, retry count, timeout, etc.
        """
        if not input.get("messages"):
            return LLMResponse(success=False, message="'messages' is required in input and must be non-empty.")
        return await self.model_context_manager(name=name, input=input, ctx=ctx, **kwargs)


# Global singleton
model_manager = ModelManagerServer()
