"""Model manager server — global singleton, mirrors skill_manager pattern."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from src.model.context import ModelContextManager
from src.model.types import ModelConfig, LLMResponse
from src.message.types import Message

if TYPE_CHECKING:
    from src.tool.types import Tool


class ModelManagerServer(BaseModel):
    """Global model manager — thin facade over ModelContextManager."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ctx = ModelContextManager()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize all provider model registrations."""
        await self._ctx.initialize()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register_model(self, config: ModelConfig) -> None:
        """Register a custom model configuration at runtime."""
        await self._ctx.register_model(config)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_model_config(self, model: str) -> Optional[ModelConfig]:
        """Return the ModelConfig for a registered model name."""
        return self._ctx.get_model_config(model)

    def list(self) -> List[str]:
        """List all registered model names."""
        return self._ctx.list()

    # async aliases for backward-compatibility with existing await call-sites
    async def alist(self) -> List[str]:
        return self._ctx.list()

    async def aget_model_config(self, model: str) -> Optional[ModelConfig]:
        return self._ctx.get_model_config(model)

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    async def __call__(
        self,
        model: str,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        response_format=None,
        stream: bool = False,
        plugins: Optional[List[Dict[str, Any]]] = None,
        max_retries: int = 3,
        caller: Optional[str] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Invoke a model with retry + fallback.

        Args:
            model:           Registered model name (e.g. "openrouter/gemini-3-flash-preview").
            messages:        Conversation messages.
            tools:           Optional tool list for function calling.
            response_format: Optional Pydantic model or dict for structured output.
            stream:          Whether to stream the response.
            plugins:         OpenRouter plugin list.
            max_retries:     Number of attempts before falling back (default 3).
            caller:          Optional caller tag for usage logs.
        """
        return await self._ctx(
            model=model,
            messages=messages,
            tools=tools,
            response_format=response_format,
            stream=stream,
            plugins=plugins,
            max_retries=max_retries,
            caller=caller,
            **kwargs,
        )


# Global singleton
model_manager = ModelManagerServer()
