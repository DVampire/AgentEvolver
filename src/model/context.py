"""Model context manager — ApiKeyPool + ModelContextManager.

Contains all model registration, client lifecycle, and invocation logic.
"""

import asyncio
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

from dotenv import load_dotenv

load_dotenv(verbose=True)

from pydantic import BaseModel

from src.model.types import ModelContext, ModelConfig
from src.response.types import Response, ResponseType
from src.model.openai.chat import ChatOpenAI
from src.model.openai.response import ResponseOpenAI
from src.model.openai.transcribe import TranscribeOpenAI
from src.model.openai.embedding import EmbeddingOpenAI
from src.model.openrouter.chat import ChatOpenRouter
from src.model.anthropic.chat import ChatAnthropic
from src.model.aws_claude.chat import ChatAwsClaude
from src.model.aws_claude.native import ChatAwsClaudeNative
from src.model.google.chat import ChatGoogle
from src.model.newapi.chat import ChatNewAPI
from src.model.newapi.response import ResponseNewAPI
from src.message.types import Message
from src.logger import logger
from src.utils import hvac_client

if TYPE_CHECKING:
    from src.tool.types import Tool


# ---------------------------------------------------------------------------
# ApiKeyPool
# ---------------------------------------------------------------------------


class ApiKeyPool:
    """Thread-safe round-robin API key pool.

    Each provider registers its key env-var (which may be a single key or a
    comma-separated list) and an optional base-URL env-var. Callers obtain the
    next key via `get_key(provider)`.
    """

    def __init__(self):
        self._keys: Dict[str, List[str]] = {}
        self._bases: Dict[str, Optional[str]] = {}
        self._indices: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _parse_keys(env_var: str) -> List[str]:
        raw = hvac_client.get(env_var)
        return [k.strip() for k in raw.split(",") if k.strip()]

    def register(
        self,
        provider: str,
        key_env: str,
        base_env: Optional[str] = None,
        default_base: Optional[str] = None,
    ) -> "ApiKeyPool":
        self._keys[provider] = self._parse_keys(key_env)
        self._bases[provider] = (
            (hvac_client.get(base_env) or default_base) if base_env else default_base
        )
        self._indices[provider] = 0
        return self

    async def get_base(self, provider: str) -> Optional[str]:
        return self._bases.get(provider)

    async def get_key(self, provider: str) -> Optional[str]:
        async with self._lock:
            keys = self._keys.get(provider, [])
            if not keys:
                return None
            idx = self._indices.get(provider, 0)
            key = keys[idx]
            self._indices[provider] = (idx + 1) % len(keys)
            return key


# ---------------------------------------------------------------------------
# ModelContextManager
# ---------------------------------------------------------------------------


class ModelContextManager:
    """Central registry and invoker for all LLM models.

    Responsibilities:
    1. Register and store model configurations.
    2. Manage provider API-key pools.
    3. Provide a unified invocation interface with retry + fallback.
    """

    def __init__(self):
        self.models: Dict[str, ModelConfig] = {}
        self.model_clients: Dict[str, Any] = {}
        self._key_pool = ApiKeyPool()
        self._current_caller: Optional[str] = None

        # Defaults
        self.max_tokens: int = 32768
        self.default_temperature: float = 0.7
        self.default_timeout: float = 600.0
        self.default_reasoning: Dict[str, Any] = {"reasoning_effort": "high"}
        self.default_plugins: Optional[List[Dict[str, Any]]] = [
            {"id": "file-parser", "pdf": {"engine": "mistral-ocr"}},
            {"id": "web", "max_results": 10},
            {"id": "response-healing"},
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self):
        (
            self._key_pool.register("openai", "OPENAI_API_KEY", "OPENAI_API_BASE", "")
            .register("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_API_BASE", "")
            .register("anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_API_BASE", "")
            .register("aws_claude", "AWS_CLAUDE_API_KEY", "AWS_CLAUDE_API_BASE", "")
            .register("google", "GOOGLE_API_KEY", "GOOGLE_API_BASE", "")
            .register("newapi", "NEWAPI_API_KEY", "NEWAPI_API_BASE", "")
            .register(
                "int_openrouter",
                "INT_OPENROUTER_API_KEY",
                "INT_OPENROUTER_API_BASE",
                "",
            )
        )
        await self._initialize_openai_models()
        await self._initialize_openrouter_models()
        await self._initialize_internal_openrouter_models()
        await self._initialize_anthropic_models()
        await self._initialize_aws_claude_models()
        await self._initialize_google_models()
        await self._initialize_newapi_models()
        logger.info(
            f"| Model context manager initialized with {len(self.models)} models."
        )

    # ------------------------------------------------------------------
    # Provider initialization
    # ------------------------------------------------------------------

    async def _initialize_openai_models(self):
        chat_models = [
            {
                "model_name": "openai/gpt-4o",
                "model_id": "gpt-4o",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-4.1",
            },
            {
                "model_name": "openai/gpt-4.1",
                "model_id": "gpt-4.1",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-4o",
            },
        ]
        response_models = [
            {
                "model_name": "openai/gpt-5",
                "model_id": "gpt-5",
                "model_type": "responses",
                "reasoning": self.default_reasoning,
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/o3",
            },
            {
                "model_name": "openai/gpt-5.1",
                "model_id": "gpt-5.1",
                "model_type": "responses",
                "reasoning": self.default_reasoning,
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-5",
            },
            {
                "model_name": "openai/o3",
                "model_id": "o3",
                "model_type": "responses",
                "reasoning": self.default_reasoning,
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-5.1",
            },
            {
                "model_name": "openai/o3-mini",
                "model_id": "o3-mini",
                "model_type": "responses",
                "reasoning": self.default_reasoning,
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-5.1",
            },
            {
                "model_name": "openai/gpt-5.2",
                "model_id": "gpt-5.1",
                "model_type": "responses",
                "reasoning": self.default_reasoning,
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-5",
            },
            {
                "model_name": "openai/gpt-5.3",
                "model_id": "gpt-5.3",
                "model_type": "responses",
                "reasoning": self.default_reasoning,
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-5",
            },
            {
                "model_name": "openai/gpt-5.4",
                "model_id": "gpt-5.4",
                "model_type": "responses",
                "reasoning": {"reasoning": {"effort": "high"}},
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-5",
            },
            {
                "model_name": "openai/gpt-5.4-pro",
                "model_id": "gpt-5.4-pro",
                "model_type": "responses",
                "reasoning": {"reasoning": {"effort": "high"}},
                "max_output_tokens": self.max_tokens,
                "fallback_model": "openai/gpt-5.4",
            },
        ]
        transcribe_models = [
            {
                "model_name": "openai/gpt-4o-transcribe",
                "model_id": "gpt-4o-transcribe",
                "model_type": "transcriptions",
                "fallback_model": "openai/gpt-4o-transcribe",
            },
        ]
        embedding_models = [
            {
                "model_name": "openai/text-embedding-3-small",
                "model_id": "text-embedding-3-small",
                "model_type": "embeddings",
                "fallback_model": "openai/text-embedding-3-large",
            },
            {
                "model_name": "openai/text-embedding-3-large",
                "model_id": "text-embedding-3-large",
                "model_type": "embeddings",
                "fallback_model": "openai/text-embedding-3-large",
            },
            {
                "model_name": "openai/text-embedding-ada-002",
                "model_id": "text-embedding-ada-002",
                "model_type": "embeddings",
                "fallback_model": "openai/text-embedding-3-large",
            },
        ]

        api_base = await self._key_pool.get_base("openai")
        api_key = await self._key_pool.get_key("openai")

        for m in chat_models:
            cfg = ModelConfig(
                model_name=m["model_name"],
                model_id=m["model_id"],
                model_type=m["model_type"],
                provider="openai",
                key_pool_name="openai",
                api_base=api_base,
                api_key=api_key,
                temperature=m.get("temperature"),
                max_completion_tokens=m.get("max_completion_tokens"),
                timeout=m.get("timeout", self.default_timeout),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=m.get("fallback_model"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

        for m in response_models:
            cfg = ModelConfig(
                model_name=m["model_name"],
                model_id=m["model_id"],
                model_type=m["model_type"],
                provider="openai",
                key_pool_name="openai",
                api_base=api_base,
                api_key=api_key,
                reasoning=m.get("reasoning"),
                max_output_tokens=m.get("max_output_tokens"),
                timeout=m.get("timeout", self.default_timeout),
                supports_streaming=False,
                supports_functions=False,
                supports_vision=True,
                output_version=None,
                fallback_model=m.get("fallback_model"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

        for m in transcribe_models:
            cfg = ModelConfig(
                model_name=m["model_name"],
                model_id=m["model_id"],
                model_type=m["model_type"],
                provider="openai",
                key_pool_name="openai",
                api_base=api_base,
                api_key=api_key,
                timeout=m.get("timeout", self.default_timeout),
                supports_streaming=False,
                supports_functions=False,
                supports_vision=False,
                output_version=None,
                fallback_model=m.get("fallback_model"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

        for m in embedding_models:
            cfg = ModelConfig(
                model_name=m["model_name"],
                model_id=m["model_id"],
                model_type=m["model_type"],
                provider="openai",
                key_pool_name="openai",
                api_base=api_base,
                api_key=api_key,
                timeout=m.get("timeout", self.default_timeout),
                supports_streaming=False,
                supports_functions=False,
                supports_vision=False,
                output_version=None,
                fallback_model=m.get("fallback_model"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    async def _initialize_openrouter_models(self):
        _r = lambda enabled=True: {"reasoning": {"enabled": enabled}}
        chat_models = [
            {
                "model_name": "openrouter/gpt-4o",
                "model_id": "openai/gpt-4o",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gpt-4.1",
                "model_id": "openai/gpt-4.1",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gpt-5",
                "model_id": "openai/gpt-5",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gpt-5.1",
                "model_id": "openai/gpt-5.1",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gpt-5.2",
                "model_id": "openai/gpt-5.2",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gpt-5.3",
                "model_id": "openai/gpt-5.3",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gpt-5.4",
                "model_id": "openai/gpt-5.4",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gpt-5.4-pro",
                "model_id": "openai/gpt-5.4-pro",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/o3",
                "model_id": "openai/o3",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": 1.0,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/o3-mini",
                "model_id": "openai/o3-mini",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": 1.0,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gpt-5.3-codex",
                "model_id": "openai/gpt-5.3-codex",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/claude-sonnet-3.5",
                "model_id": "anthropic/claude-3.5-sonnet",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/claude-sonnet-3.7",
                "model_id": "anthropic/claude-3.7-sonnet",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/claude-sonnet-4",
                "model_id": "anthropic/claude-sonnet-4",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/claude-opus-4",
                "model_id": "anthropic/claude-opus-4",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/claude-sonnet-4.5",
                "model_id": "anthropic/claude-sonnet-4.5",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/claude-opus-4.5",
                "model_id": "anthropic/claude-opus-4.5",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/claude-sonnet-4.6",
                "model_id": "anthropic/claude-sonnet-4.6",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/claude-opus-4.6",
                "model_id": "anthropic/claude-opus-4.6",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                # reasoning is capped at 8k thinking tokens so long chains of
                # thought can't consume the whole max_completion_tokens budget
                # and truncate the structured-output JSON body (which produced
                # "Unterminated string" parse failures). extra_body is passed
                # straight through to OpenRouter as the top-level `reasoning`.
                "model_name": "openrouter/claude-opus-4.8",
                "model_id": "anthropic/claude-opus-4.8",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": {"max_tokens": 8000}},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gemini-2.5-flash",
                "model_id": "google/gemini-2.5-flash",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gemini-2.5-pro",
                "model_id": "google/gemini-2.5-pro",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gemini-3-pro-preview",
                "model_id": "google/gemini-3-pro-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gemini-3.1-pro-preview",
                "model_id": "google/gemini-3.1-pro-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gemini-3-flash-preview",
                "model_id": "google/gemini-3-flash-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                # Fast, cheap flash used as the universal fallback for every
                # openrouter model above. Its own fallback points elsewhere to
                # avoid a self-loop.
                "model_name": "openrouter/gemini-3.5-flash",
                "model_id": "google/gemini-3.5-flash",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3-flash-preview",
            },
            {
                "model_name": "openrouter/gemini-2.5-flash-plugins",
                "model_id": "google/gemini-2.5-flash",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "plugins": self.default_plugins,
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gemini-3-flash-preview-plugins",
                "model_id": "google/gemini-3-flash-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "plugins": self.default_plugins,
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gemini-3.1-flash-lite-preview",
                "model_id": "google/gemini-3.1-flash-lite-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gemini-3.1-flash-lite-preview-plugins",
                "model_id": "google/gemini-3.1-flash-lite-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "plugins": self.default_plugins,
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/gemini-3.1-pro-preview-plugins",
                "model_id": "google/gemini-3.1-pro-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "plugins": self.default_plugins,
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/qwen3-coder",
                "model_id": "qwen/qwen3-coder",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/qwen3-max",
                "model_id": "qwen/qwen3-max",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/deepseek-v3.2",
                "model_id": "deepseek/deepseek-v3.2",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
            {
                "model_name": "openrouter/grok-4.1-fast",
                "model_id": "x-ai/grok-4.1-fast",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3.5-flash",
            },
        ]

        api_base = await self._key_pool.get_base("openrouter")
        api_key = await self._key_pool.get_key("openrouter")

        for m in chat_models:
            cfg = ModelConfig(
                model_name=m["model_name"],
                model_id=m["model_id"],
                model_type=m["model_type"],
                provider="openrouter",
                key_pool_name="openrouter",
                api_base=api_base,
                api_key=api_key,
                reasoning=m.get("reasoning") or None,
                plugins=m.get("plugins") or None,
                temperature=m.get("temperature"),
                max_completion_tokens=m.get("max_completion_tokens"),
                timeout=m.get("timeout", self.default_timeout),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=m.get("fallback_model"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    async def _initialize_internal_openrouter_models(self):
        _r = lambda: {"reasoning": {"enabled": True}}
        chat_models = [
            {
                "model_name": "int_openrouter/o3-mini",
                "model_id": "openai/o3-mini",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": 1.0,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gpt-5.4",
            },
            {
                "model_name": "int_openrouter/gpt-5.3-codex",
                "model_id": "openai/gpt-5.3-codex",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gpt-5.4",
            },
            {
                "model_name": "int_openrouter/gpt-5.4",
                "model_id": "openai/gpt-5.4",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gpt-5.4",
            },
            {
                "model_name": "int_openrouter/gpt-5.4-pro",
                "model_id": "openai/gpt-5.4-pro",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gpt-5.4",
            },
            {
                "model_name": "int_openrouter/gpt-5.5",
                "model_id": "openai/gpt-5.5",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gpt-5.4",
            },
            {
                "model_name": "int_openrouter/gpt-5.5-pro",
                "model_id": "openai/gpt-5.5-pro",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gpt-5.4",
            },
            {
                "model_name": "int_openrouter/claude-sonnet-4.6",
                "model_id": "anthropic/claude-sonnet-4.6",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gpt-5.4",
            },
            {
                "model_name": "int_openrouter/claude-opus-4.6",
                "model_id": "anthropic/claude-opus-4.6",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gpt-5.4",
            },
            {
                "model_name": "int_openrouter/gemini-3.1-pro-preview",
                "model_id": "google/gemini-3.1-pro-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gemini-3.1-pro-preview",
            },
            {
                "model_name": "int_openrouter/gemini-3-flash-preview",
                "model_id": "google/gemini-3-flash-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gemini-3-flash-preview",
            },
            {
                "model_name": "int_openrouter/gemini-3.1-flash-lite-preview",
                "model_id": "google/gemini-3.1-flash-lite-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gemini-3-flash-preview-plugins",
            },
            {
                "model_name": "int_openrouter/gemini-3.1-flash-lite-preview-plugins",
                "model_id": "google/gemini-3.1-flash-lite-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "plugins": self.default_plugins,
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gemini-3-flash-preview-plugins",
            },
            {
                "model_name": "int_openrouter/gemini-3.1-pro-preview-plugins",
                "model_id": "google/gemini-3.1-pro-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "plugins": self.default_plugins,
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/gemini-3-flash-preview-plugins",
            },
            {
                "model_name": "int_openrouter/grok-4.1-fast",
                "model_id": "x-ai/grok-4.1-fast",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "int_openrouter/grok-4.1-fast",
            },
        ]

        api_base = await self._key_pool.get_base("int_openrouter")
        api_key = await self._key_pool.get_key("int_openrouter")

        for m in chat_models:
            cfg = ModelConfig(
                model_name=m["model_name"],
                model_id=m["model_id"],
                model_type=m["model_type"],
                provider="openrouter",
                key_pool_name="int_openrouter",
                api_base=api_base,
                api_key=api_key,
                reasoning=m.get("reasoning") or None,
                plugins=m.get("plugins") or None,
                temperature=m.get("temperature"),
                max_completion_tokens=m.get("max_completion_tokens"),
                timeout=m.get("timeout", self.default_timeout),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=m.get("fallback_model"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    async def _initialize_anthropic_models(self):
        chat_models = [
            {
                "model_name": "anthropic/claude-sonnet-3.7",
                "model_id": "claude-3-7-sonnet-20250219",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "anthropic/claude-sonnet-4.5",
            },
            {
                "model_name": "anthropic/claude-sonnet-4",
                "model_id": "claude-sonnet-4-20250514",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "anthropic/claude-sonnet-4.5",
            },
            {
                "model_name": "anthropic/claude-sonnet-4.5",
                "model_id": "claude-sonnet-4-5-20250929",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "anthropic/claude-sonnet-4.5",
            },
        ]

        api_base = await self._key_pool.get_base("anthropic")
        api_key = await self._key_pool.get_key("anthropic")

        for m in chat_models:
            cfg = ModelConfig(
                model_name=m["model_name"],
                model_id=m["model_id"],
                model_type=m["model_type"],
                provider="anthropic",
                key_pool_name="anthropic",
                api_base=api_base,
                api_key=api_key,
                temperature=m.get("temperature"),
                max_completion_tokens=m.get("max_completion_tokens"),
                timeout=m.get("timeout", self.default_timeout),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=m.get("fallback_model"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    async def _initialize_aws_claude_models(self):
        """Initialize AWS Claude models (OpenAI-compatible /chat/completions gateway)."""
        chat_models = [
            {
                "model_name": "aws_claude/claude-opus-4.6",
                "model_id": "claude-opus-4-6",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "aws_claude/claude-opus-4.6",
            },
            {
                "model_name": "aws_claude/claude-opus-4.7",
                "model_id": "claude-opus-4-7",
                "model_type": "chat/completions",
                "temperature": None,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "aws_claude/claude-opus-4.6",
            },
            {
                # Anthropic-native /v1/messages route: the gateway forwards
                # `thinking`/`output_config` there (the /chat/completions
                # route silently drops them), so adaptive thinking works.
                "model_name": "aws_claude/claude-opus-4.8",
                "model_id": "claude-opus-4-8",
                "model_type": "messages",
                "temperature": None,
                "max_completion_tokens": self.max_tokens,
                "reasoning": {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": "high"},
                },
                "fallback_model": "aws_claude/claude-opus-4.7",
            },
        ]

        api_base = await self._key_pool.get_base("aws_claude")
        api_key = await self._key_pool.get_key("aws_claude")

        # Register AWS Claude models
        for model in chat_models:
            config = ModelConfig(
                model_name=model["model_name"],
                model_id=model["model_id"],
                model_type=model["model_type"],
                provider="aws_claude",
                key_pool_name="aws_claude",
                api_base=api_base,
                api_key=api_key,
                temperature=model.get("temperature"),
                reasoning=model.get("reasoning"),
                max_completion_tokens=model.get("max_completion_tokens"),
                timeout=model.get("timeout", self.default_timeout),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=model.get("fallback_model"),
            )
            self.models[config.model_name] = config
            await self._create_client(config)

    async def _initialize_google_models(self):
        _r = lambda: {"reasoning": {"enabled": True}}
        chat_models = [
            {
                "model_name": "google/gemini-2.5-flash",
                "model_id": "gemini-2.5-flash",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3-flash-preview",
            },
            {
                "model_name": "google/gemini-2.5-pro",
                "model_id": "gemini-2.5-pro",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3-flash-preview",
            },
            {
                "model_name": "google/gemini-3-pro-preview",
                "model_id": "gemini-3-pro-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3-flash-preview",
            },
            {
                "model_name": "google/gemini-3.1-pro-preview",
                "model_id": "gemini-3.1-pro-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3-flash-preview",
            },
            {
                "model_name": "google/gemini-3-flash-preview",
                "model_id": "gemini-3-flash-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3-flash-preview",
            },
            {
                "model_name": "google/gemini-3.1-flash-lite-preview",
                "model_id": "gemini-3.1-flash-lite-preview",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/gemini-3-flash-preview",
            },
        ]

        api_base = await self._key_pool.get_base("google")
        api_key = await self._key_pool.get_key("google")

        for m in chat_models:
            cfg = ModelConfig(
                model_name=m["model_name"],
                model_id=m["model_id"],
                model_type=m["model_type"],
                provider="google",
                key_pool_name="google",
                api_base=api_base,
                api_key=api_key,
                reasoning=m.get("reasoning") or None,
                temperature=m.get("temperature"),
                max_completion_tokens=m.get("max_completion_tokens"),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=None,
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    async def _initialize_newapi_models(self):
        _r = lambda: {"reasoning": {"enabled": True}}
        chat_models = [
            {
                "model_name": "newapi/gemini-3.1-pro-preview",
                "model_id": "gemini-3.1-pro-preview",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/grok-4.1-fast",
            },
            {
                "model_name": "newapi/gemini-3.1-flash-lite-preview",
                "model_id": "gemini-3.1-flash-lite-preview",
                "model_type": "chat/completions",
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/grok-4.1-fast",
            },
            {
                "model_name": "newapi/gpt-5.4",
                "model_id": "gpt-5.4",
                "model_type": "responses",
                "reasoning": self.default_reasoning,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/grok-4.1-fast",
            },
            {
                "model_name": "newapi/gpt-5.4-pro",
                "model_id": "gpt-5.4-pro",
                "model_type": "responses",
                "timeout": 3600.0,
                "reasoning": self.default_reasoning,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/grok-4.1-fast",
            },
            {
                "model_name": "newapi/gpt-5-nano",
                "model_id": "gpt-5-nano",
                "model_type": "responses",
                "reasoning": self.default_reasoning,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/grok-4.1-fast",
            },
            {
                "model_name": "newapi/o3-mini",
                "model_id": "o3-mini",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": 1.0,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "newapi/gpt-5.4",
            },
            {
                "model_name": "newapi/claude-opus-4.6",
                "model_id": "claude-opus-4-6",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/grok-4.1-fast",
            },
            {
                "model_name": "newapi/grok-4.1-fast",
                "model_id": "grok-4.1-fast",
                "model_type": "chat/completions",
                "reasoning": {"reasoning": _r()},
                "temperature": self.default_temperature,
                "max_completion_tokens": self.max_tokens,
                "fallback_model": "openrouter/grok-4.1-fast",
            },
        ]

        api_base = await self._key_pool.get_base("newapi")
        api_key = await self._key_pool.get_key("newapi")

        for m in chat_models:
            cfg = ModelConfig(
                model_name=m["model_name"],
                model_id=m["model_id"],
                model_type=m["model_type"],
                provider="newapi",
                key_pool_name="newapi",
                api_base=api_base,
                api_key=api_key,
                temperature=m.get("temperature"),
                max_completion_tokens=m.get("max_completion_tokens"),
                reasoning=m.get("reasoning") or None,
                timeout=m.get("timeout", self.default_timeout),
                supports_streaming=True,
                supports_functions=True,
                supports_vision=True,
                output_version=None,
                fallback_model=m.get("fallback_model"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    async def _create_client(self, config: ModelConfig) -> None:
        self.model_clients[config.model_name] = await self._build_client(config)
        logger.info(f"| Created client for {config.model_name}")

    async def _build_client(self, config: ModelConfig):
        if config.provider == "newapi":
            if config.model_type == "chat/completions":
                return ChatNewAPI(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    temperature=config.temperature or self.default_temperature,
                    max_completion_tokens=config.max_completion_tokens
                    or self.max_tokens,
                )
            elif config.model_type == "responses":
                return ResponseNewAPI(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    reasoning=config.reasoning or None,
                    max_output_tokens=config.max_output_tokens or self.max_tokens,
                )
            raise ValueError(
                f"Unsupported model type {config.model_type} for New-API provider"
            )
        elif config.provider == "openrouter":
            if config.model_type == "chat/completions":
                return ChatOpenRouter(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    reasoning=config.reasoning or None,
                    plugins=config.plugins or None,
                    temperature=config.temperature or self.default_temperature,
                    max_completion_tokens=config.max_completion_tokens
                    or self.max_tokens,
                )
            raise ValueError(
                f"Unsupported model type {config.model_type} for OpenRouter provider"
            )
        elif config.provider == "anthropic":
            if config.model_type == "chat/completions":
                return ChatAnthropic(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    reasoning=config.reasoning or None,
                    temperature=config.temperature or self.default_temperature,
                    max_tokens=config.max_completion_tokens or self.max_tokens,
                )
            raise ValueError(
                f"Unsupported model type {config.model_type} for Anthropic provider"
            )
        elif config.provider == "aws_claude":
            if config.model_type == "chat/completions":
                return ChatAwsClaude(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    reasoning=config.reasoning if config.reasoning else None,
                    temperature=config.temperature,
                    max_completion_tokens=config.max_completion_tokens
                    or self.max_tokens,
                )
            elif config.model_type == "messages":
                # Anthropic-native route — required for thinking support.
                # temperature is passed through as-is (None for Opus 4.7+,
                # which rejects the parameter).
                return ChatAwsClaudeNative(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    reasoning=config.reasoning if config.reasoning else None,
                    temperature=config.temperature,
                    max_tokens=config.max_completion_tokens or self.max_tokens,
                )
            else:
                raise ValueError(
                    f"Unsupported model type {config.model_type} for AWS Claude provider"
                )
        elif config.provider == "google":
            if config.model_type == "chat/completions":
                return ChatGoogle(
                    model=config.model_id,
                    api_key=config.api_key,
                    reasoning=config.reasoning or None,
                    temperature=config.temperature or self.default_temperature,
                    max_output_tokens=config.max_completion_tokens or self.max_tokens,
                )
            raise ValueError(
                f"Unsupported model type {config.model_type} for Google provider"
            )
        elif config.model_type == "responses":
            return ResponseOpenAI(
                model=config.model_id,
                api_key=config.api_key,
                base_url=config.api_base,
                reasoning=config.reasoning or None,
                max_output_tokens=config.max_output_tokens or self.max_tokens,
            )
        elif config.model_type == "transcriptions":
            return TranscribeOpenAI(
                model=config.model_id, api_key=config.api_key, base_url=config.api_base
            )
        elif config.model_type == "embeddings":
            return EmbeddingOpenAI(
                model=config.model_id, api_key=config.api_key, base_url=config.api_base
            )
        else:
            return ChatOpenAI(
                model=config.model_id,
                api_key=config.api_key,
                base_url=config.api_base,
                temperature=config.temperature or self.default_temperature,
                reasoning=config.reasoning or None,
                max_completion_tokens=config.max_completion_tokens or self.max_tokens,
            )

    async def _get_client(self, model: str):
        client = self.model_clients.get(model)
        if client:
            model_config = self.models.get(model)
            pool_name = (
                (model_config.key_pool_name or model_config.provider)
                if model_config
                else None
            )
            key = await self._key_pool.get_key(pool_name) if pool_name else None
            if key:
                client.set_api_key(key)
        return client

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register_model(self, config: ModelConfig) -> None:
        if config.provider not in [
            "openai",
            "openrouter",
            "anthropic",
            "google",
            "newapi",
        ]:
            raise ValueError(f"Unsupported provider: {config.provider}")
        self.models[config.model_name] = config
        await self._create_client(config)
        logger.info(f"Registered model: {config.model_name}")

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_model_config(self, model: str) -> Optional[ModelConfig]:
        return self.models.get(model)

    def list(self) -> List[str]:
        return list(self.models.keys())

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    def _log_usage(self, model_name: str, result: Response) -> None:
        if not result.success:
            return
        # Prefer the structured TokenUsage field; fall back to raw dict for older code paths
        usage = result.usage
        if usage is None and result.data:
            from src.model.types import TokenUsage

            raw = (result.data or {}).get("usage")
            usage = TokenUsage.from_raw(raw) if raw else None
        if usage is None:
            return
        parts = [
            f"model={model_name}",
            f"in={usage.input_tokens}",
            f"out={usage.output_tokens}",
            f"total={usage.total}",
        ]
        if usage.cache_write_tokens:
            parts.append(f"cache_write={usage.cache_write_tokens}")
        if usage.cache_read_tokens:
            parts.append(f"cache_read={usage.cache_read_tokens}")
        if self._current_caller:
            parts.append(f"caller={self._current_caller}")
        logger.info(f"| 💰 {', '.join(parts)}")

    async def _call_client(
        self,
        client,
        model_config,
        messages,
        tools,
        response_format,
        stream,
        plugins,
        kwargs,
    ) -> Response:
        if model_config and model_config.model_type == "transcriptions":
            return await client(messages=messages, **kwargs)
        elif model_config and model_config.model_type == "embeddings":
            return await client(
                messages=messages,
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k not in ("tools", "response_format", "stream")
                },
            )
        else:
            call_kwargs = dict(
                messages=messages,
                tools=tools,
                response_format=response_format,
                stream=stream,
                **kwargs,
            )
            if model_config and model_config.provider == "openrouter":
                call_kwargs["plugins"] = plugins
            return await client(**call_kwargs)

    async def __call__(
        self,
        name: str,
        input: Dict[str, Any],
        ctx: ModelContext = None,
        **kwargs: Any,
    ) -> Response:
        """Invoke a registered model by name.

        Args:
            name:  Registered model name (e.g. "openrouter/gemini-3-flash-preview").
            input: Call payload — keys: messages (required), tools, response_format,
                   stream, plugins, max_retries, caller.
            ctx:   Optional ModelContext (carries id, name, work_dir, timeout, extra).
        """
        import time as _t
        import httpx

        ctx = ModelContext.from_context(ctx)
        if not ctx.name:
            ctx = ctx.model_copy(update={"name": name})

        messages = input.get("messages", [])
        tools = input.get("tools")
        response_format = input.get("response_format")
        stream = input.get("stream", False)
        plugins = input.get("plugins")
        max_retries = input.get("max_retries", 3)
        caller = input.get("caller")

        self._current_caller = caller
        # tools + response_format may be used together: the tool schemas constrain
        # tool-call arguments; response_format constrains the final answer. A turn
        # resolves to one or the other (provider serializers handle both). On the
        # aws_claude backend, structured output is emulated as an optional "final
        # answer" tool so real tools stay callable — see ChatAwsClaude._build_params.

        if name not in self.model_clients:
            return Response(
                type=ResponseType.LLM,
                success=False,
                message=f"Model {name} not found. Available: {list(self.models.keys())}",
            )

        model_config = self.models.get(name)
        last_exc: Exception = None

        for attempt in range(max_retries):
            _start = _t.time()
            try:
                client = await self._get_client(name)
                result = await self._call_client(
                    client,
                    model_config,
                    messages,
                    tools,
                    response_format,
                    stream,
                    plugins,
                    kwargs,
                )
                self._log_usage(name, result)
                if not result.success:
                    raise Exception(result.message or "Model returned success=False")
                is_chat = not model_config or model_config.model_type not in (
                    "transcriptions",
                    "embeddings",
                )
                if is_chat and not result.message:
                    raise Exception("Model returned empty message")
                return result
            except (
                httpx.TimeoutException,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
            ) as e:
                last_exc = e
                logger.error(
                    f"| ❌ Model {name} timed out ({_t.time()-_start:.0f}s): {e}"
                )
                break
            except Exception as e:
                last_exc = e
                _elapsed = _t.time() - _start
                tag = f", caller={self._current_caller}" if self._current_caller else ""
                if attempt < max_retries - 1:
                    logger.warning(
                        f"| ⚠️ Model {name} attempt {attempt+1}/{max_retries} failed ({_elapsed:.0f}s{tag}): {e}, retrying..."
                    )
                else:
                    logger.error(
                        f"| ❌ Model {name} failed after {max_retries} attempts ({_elapsed:.0f}s{tag}): {e}"
                    )

        if model_config and model_config.fallback_model:
            fallback = model_config.fallback_model
            logger.warning(
                f"| Primary model {name} exhausted retries, falling back to {fallback}"
            )
            if fallback not in self.model_clients:
                return Response(
                    type=ResponseType.LLM,
                    success=False,
                    message=f"Primary model {name} failed and fallback {fallback} not found. Error: {last_exc}",
                )
            fallback_config = self.models.get(fallback)
            try:
                fb_client = await self._get_client(fallback)
                result = await self._call_client(
                    fb_client,
                    fallback_config,
                    messages,
                    tools,
                    response_format,
                    stream,
                    plugins,
                    kwargs,
                )
                self._log_usage(fallback, result)
                if not result.success:
                    raise Exception(result.message or "Fallback returned success=False")
                is_chat = not fallback_config or fallback_config.model_type not in (
                    "transcriptions",
                    "embeddings",
                )
                if is_chat and not result.message:
                    raise Exception("Fallback returned empty message")
                logger.info(f"| Fallback model {fallback} succeeded")
                return result
            except Exception as fallback_error:
                logger.error(
                    f"| Fallback model {fallback} also failed: {fallback_error}"
                )
                return Response(
                    type=ResponseType.LLM,
                    success=False,
                    message=f"Both {name} and fallback {fallback} failed. Primary: {last_exc}, Fallback: {fallback_error}",
                )

        return Response(type=ResponseType.LLM, success=False, message=str(last_exc))

    async def stream(
        self,
        name: str,
        input: Dict[str, Any],
        ctx: ModelContext = None,
        **kwargs: Any,
    ):
        """Stream a model invocation, yielding canonical stream events.

        Provider-agnostic: delegates to the provider client's ``stream()``, which
        normalizes its wire format into the canonical event set (see
        ``src.model.types``). No retry/fallback on the streaming path (v1) — the
        buffered ``__call__`` keeps those.
        """
        ctx = ModelContext.from_context(ctx)
        messages = input.get("messages", [])
        tools = input.get("tools")
        response_format = input.get("response_format")

        if name not in self.model_clients:
            raise ValueError(f"Model {name} not found. Available: {list(self.models.keys())}")
        client = await self._get_client(name)
        if hasattr(client, "stream"):
            async for ev in client.stream(
                messages=messages, tools=tools, response_format=response_format, **kwargs
            ):
                yield ev
        else:
            # Graceful fallback for any provider without a stream(): buffer, then
            # re-emit the final Response as canonical events (uniform interface).
            from src.model.types import buffered_response_to_events
            resp = await client(
                messages=messages, tools=tools, response_format=response_format, **kwargs
            )
            async for ev in buffered_response_to_events(resp):
                yield ev


__all__ = ["ApiKeyPool", "ModelContextManager"]
