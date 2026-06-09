from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from src.session import BaseContext


class ModelContext(BaseContext):
    """Context passed into model manager and individual model invocations."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(description="Unique session/call identifier.")
    name: Optional[str] = Field(default=None, description="Human-readable label for this invocation context.")
    work_dir: Optional[str] = Field(default=None, description="Working directory available to the caller.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this context.")


class ModelConfig(BaseModel):
    """Configuration container describing a single LLM/provider pairing."""

    model_name: str = Field(description="Human-readable name used across the codebase.")
    model_type: str = Field(description="Model type, e.g. 'chat/completions', 'responses', 'embeddings'.")
    model_id: str = Field(description="Provider-specific identifier passed to the API.")
    provider: str = Field(description="Provider slug, e.g. 'openai', 'anthropic'.")
    api_base: Optional[str] = Field(default=None, description="Override API base URL.")
    api_key: Optional[str] = Field(default=None, description="Override API key.")
    temperature: Optional[float] = Field(default=None, description="Temperature parameter for the model.")
    reasoning: Optional[Dict[str, Any]] = Field(default={
        "reasoning_effort": "high"
    }, description="Reasoning configuration.")
    plugins: Optional[List[Dict[str, Any]]] = Field(default=None, description="Plugins to use for the model.")
    max_completion_tokens: Optional[int] = Field(default=None, description="Maximum completion tokens for chat/completions models.")
    max_output_tokens: Optional[int] = Field(default=None, description="Maximum output tokens for responses API models.")
    supports_streaming: bool = Field(default=True, description="Whether streaming is supported.")
    supports_functions: bool = Field(default=False, description="Whether tool/function calling is supported.")
    supports_vision: bool = Field(default=False, description="Whether multimodal inputs are supported.")
    output_version: Optional[str] = Field(
        default=None,
        description="Optional output schema version when required by provider.",
    )
    timeout: Optional[float] = Field(default=None, description="Request timeout in seconds.")
    key_pool_name: Optional[str] = Field(default=None, description="Key pool name for round-robin key lookup. Defaults to provider if not set.")
    fallback_model: Optional[str] = Field(
        default=None,
        description="Fallback model name to use if the primary model fails due to policy/content filter errors.",
    )


class TokenUsage(BaseModel):
    """Structured token usage from a single LLM API call."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost: Optional[float] = None

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_write_tokens + self.cache_read_tokens

    @classmethod
    def from_raw(cls, raw: Optional[Dict[str, Any]]) -> Optional["TokenUsage"]:
        """Normalize provider-specific usage dicts into TokenUsage."""
        if not raw:
            return None
        # cache_read: OpenRouter returns in prompt_tokens_details.cached_tokens
        cache_read = (
            raw.get("cache_read_input_tokens") or
            (raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        )
        # cost: OpenRouter returns top-level cost field
        cost_raw = raw.get("cost")
        cost = float(cost_raw) if cost_raw is not None else None
        return cls(
            input_tokens=(
                raw.get("prompt_tokens") or raw.get("input_tokens") or
                raw.get("prompt_token_count") or 0
            ),
            output_tokens=(
                raw.get("completion_tokens") or raw.get("output_tokens") or
                raw.get("candidates_token_count") or 0
            ),
            cache_write_tokens=raw.get("cache_creation_input_tokens") or 0,
            cache_read_tokens=cache_read,
            cost=cost,
        )

    def summary_line(self, model: str = "") -> str:
        parts = [f"in={self.input_tokens}", f"out={self.output_tokens}"]
        if self.cache_write_tokens:
            parts.append(f"cache_write={self.cache_write_tokens}")
        if self.cache_read_tokens:
            parts.append(f"cache_read={self.cache_read_tokens}")
        if self.cost is not None:
            parts.append(f"cost=${self.cost:.6f}")
        prefix = f"[{model}] " if model else ""
        return f"{prefix}tokens: {', '.join(parts)}"


__all__ = ["ModelContext", "ModelConfig", "TokenUsage"]

