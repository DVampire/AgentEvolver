"""Model context manager — ApiKeyPool + ModelContextManager.

Contains all model registration, client lifecycle, and invocation logic.
"""

import asyncio
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING

from dotenv import load_dotenv

load_dotenv(verbose=True)

from pydantic import BaseModel

from agentevolver.model.types import ModelContext, ModelConfig
from agentevolver.response.types import Response, ResponseType
from agentevolver.model.openai.chat import ChatOpenAI
from agentevolver.model.openai.response import ResponseOpenAI
from agentevolver.model.openai.transcribe import TranscribeOpenAI
from agentevolver.model.openai.embedding import EmbeddingOpenAI
from agentevolver.model.openrouter.chat import ChatOpenRouter
from agentevolver.model.llm_hub.chat import ChatLLMHub
from agentevolver.model.llm_hub.response import ResponseLLMHub
from agentevolver.model.anthropic.chat import ChatAnthropic
from agentevolver.model.google.chat import ChatGoogle
from agentevolver.message.types import Message
from agentevolver.model.pressure import (
    DEFAULT_CONTEXT_WINDOW,
    ContextOverflowError,
    prepare_messages,
    provider_rejected_for_length,
    resolve_request_token_estimator,
)
from agentevolver.logger import logger
from agentevolver.utils import hvac_client

if TYPE_CHECKING:
    from agentevolver.tool.types import Tool


# --------------------------------------------------------------------------- #
# Retry timing
# --------------------------------------------------------------------------- #
#: First wait after a failed attempt, doubling from there.
_RETRY_INITIAL_DELAY = 1.0
#: Cap on one wait. A provider that is down stays down; waiting five minutes between
#: attempts turns a failed call into a hung agent.
_RETRY_MAX_DELAY = 30.0
#: Fraction of the computed delay that is randomised, in both directions. Without it every
#: agent that hit the same rate limit retries in lockstep and re-creates the burst that
#: caused it.
_RETRY_JITTER = 0.25

#: Flat wait before retrying a transient empty completion. Unlike a rate limit or a
#: half-open connection — where retrying sooner just re-triggers the same failure and the
#: exponential backoff is the point — an empty completion is a momentary upstream blip that
#: the next call almost always answers, so a short fixed pause recovers it without burning
#: the run's wall clock. It never rides out a sustained bad window (no retry budget can);
#: that is the relay's to fix.
_EMPTY_COMPLETION_RETRY_DELAY = 0.5


def _is_transient_empty(error: Exception) -> bool:
    """True for the "Model returned empty message" / "Fallback returned empty message"
    failures — a transient empty completion that recovers on an immediate retry, as
    opposed to a rate limit, timeout, or dropped connection that needs real backoff."""
    return "empty message" in str(error).lower()


def _retry_delay(attempt: int) -> float:
    """Seconds to wait before ``attempt`` (1-based), with exponential backoff and jitter."""
    import random

    exponential = min(_RETRY_INITIAL_DELAY * (2 ** max(attempt - 1, 0)), _RETRY_MAX_DELAY)
    jitter = 1.0 - _RETRY_JITTER + 2 * _RETRY_JITTER * random.random()
    return min(exponential * jitter, _RETRY_MAX_DELAY)


#: Default attempts before a model call is treated as failed. A route with a flaky upstream
#: raises its own via `ModelConfig.max_retries`; a per-call value overrides both.
_DEFAULT_MAX_RETRIES = 3


def _resolve_max_retries(per_call: Optional[int], model_config: Any) -> int:
    """Effective attempt budget: an explicit per-call value wins; else the model's own
    ceiling (set for a flaky route); else the default. Kept in one place so both the
    buffered and streaming call paths resolve it identically."""
    if per_call is not None:
        return per_call
    configured = getattr(model_config, "max_retries", None)
    # `is not None`, not truthiness: a configured 0 (a route that wants a single no-retry
    # attempt) is an explicit choice, and `0 or 3` would silently turn it into 3.
    return configured if configured is not None else _DEFAULT_MAX_RETRIES


def _prepare_request_messages(
    *,
    messages: List[Any],
    tools: Optional[List[Any]],
    response_format: Any,
    model_config: Optional[ModelConfig],
    request_input: Dict[str, Any],
    default_output_tokens: int,
    call_kwargs: Optional[Dict[str, Any]] = None,
    model_name: str = "",
):
    """Apply the same deterministic pressure policy to buffered and streamed routes.

    Raises ``ContextOverflowError`` when the prepared request still exceeds the window.
    Raising rather than returning a flag for the reason this repository keeps rediscovering:
    three routes prepare a request here, and a flag only two of them read is a check that
    the third silently does not have.
    """
    configured_window = (
        request_input.get("context_window")
        or (getattr(model_config, "context_window", None) if model_config else None)
        or DEFAULT_CONTEXT_WINDOW
    )
    call_kwargs = call_kwargs or {}
    reserved_output = request_input.get("reserved_output_tokens")
    if reserved_output is None:
        reserved_output = next((
            call_kwargs.get(name)
            for name in ("max_completion_tokens", "max_output_tokens", "max_tokens")
            if call_kwargs.get(name) is not None
        ), None)
    if reserved_output is None and model_config is not None:
        reserved_output = (
            model_config.max_completion_tokens or model_config.max_output_tokens
        )
    if reserved_output is None:
        reserved_output = default_output_tokens
    prepared = prepare_messages(
        messages,
        tools=tools,
        response_format=response_format,
        context_window=int(configured_window),
        reserved_output_tokens=int(reserved_output),
        prune_ratio=float(request_input.get("request_prune_ratio", 0.85)),
        target_ratio=float(request_input.get("request_target_ratio", 0.75)),
        token_estimator=resolve_request_token_estimator(
            provider=getattr(model_config, "provider", "") if model_config else "",
            model=getattr(model_config, "model_id", "") if model_config else "",
        ),
    )
    pressure = prepared.pressure
    policy = request_input.get("compaction_policy")
    if isinstance(policy, dict) and policy:
        pressure["compaction_policy"] = dict(policy)
    if pressure.get("over_capacity"):
        raise ContextOverflowError(
            f"The request does not fit {model_name or 'this model'}: about "
            f"{pressure['estimated_tokens_after']} tokens against an input capacity of "
            f"{pressure['input_capacity_tokens']}"
            + (f", after excerpting {len(pressure['pruned_message_indices'])} tool "
               f"result(s)" if pressure["pruned_message_indices"] else
               ", and no tool result was large enough to excerpt")
            + ". Only tool results may be reduced at this boundary, so what remains is "
              "instructions and reasoning; the conversation itself has to be compacted.",
            pressure=pressure,
        )
    return prepared


async def _record_retry(
    session_id: Optional[str], model: str, attempt: int, total: int,
    error: str, delay: Optional[float], caller: Optional[str],
) -> None:
    """Write one failed model attempt into the trace.

    A retried call reports the outcome of its last attempt and nothing else, so a
    trajectory that says ``success`` can be the third try of three. That matters more here
    than in most systems: these trajectories are training data, and a sample labelled
    "the model got it right" when the model actually failed twice first is not the sample
    it claims to be.

    Never raises and never blocks the call it is recording — a trace that cannot be written
    is worth strictly less than the request in flight.
    """
    if not session_id:
        return
    try:
        from agentevolver.trace.server import trace_manager
        from agentevolver.trace.types import TraceEvent, TraceEventType

        await trace_manager.emit(TraceEvent(
            event_type=TraceEventType.CUSTOM,
            session_id=session_id,
            label="model retry",
            message=f"{model} attempt {attempt}/{total} failed: {error}",
            success=False,
            error=error,
            metadata={
                "type": "llm_retry",
                "model": model,
                "attempt": attempt,
                "max_attempts": total,
                "delay_seconds": delay,
                "caller": caller,
            },
        ))
    except Exception as trace_error:  # noqa: BLE001 — recording must not break the call
        logger.debug(f"| model retry not recorded in the trace: {trace_error}")


async def _record_request_snapshot(
    *,
    session_id: Optional[str],
    requested_model: str,
    routed_model: str,
    model_config: Optional[ModelConfig],
    client: Any,
    messages: List[Any],
    tools: Optional[List[Any]],
    response_format: Any,
    request_input: Dict[str, Any],
    call_kwargs: Dict[str, Any],
    stream: bool,
    attempt: int,
    route_index: int,
    pressure: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Commit the effective model request before provider dispatch.

    This is intentionally centralized beside retry/fallback. Recording in ``Agent``
    would capture the requested alias but miss the provider route selected after a
    fallback; recording in each provider would duplicate redaction and schema logic.

    Interactive Trace remains a side channel. Training/high-risk profiles make this
    boundary required: the provider is not called unless the request fact is durable.
    """
    profile = request_input.get("trace_integrity_profile")
    if not session_id:
        from agentevolver.trace.checkpoint import (
            TraceIntegrityError,
            resolve_integrity_profile,
        )

        selected = resolve_integrity_profile(profile)
        if selected.required:
            raise TraceIntegrityError(
                "Trace integrity checkpoint 'before_model_request' requires a real "
                f"session id under profile {selected.value!r}"
            )
        return None
    try:
        from agentevolver.trace.request import RequestSnapshot
        from agentevolver.trace.server import trace_manager
        from agentevolver.trace.types import model_request_event

        snapshot = RequestSnapshot.capture(
            requested_model=requested_model,
            routed_model=routed_model,
            model_config=model_config,
            client=client,
            messages=messages,
            tools=tools,
            response_format=response_format,
            request_input=request_input,
            call_kwargs=call_kwargs,
            stream=stream,
            pressure=pressure,
        )
        coordinates = request_input.get("trace_context") or {}
        event = model_request_event(
            session_id=session_id,
            snapshot=snapshot,
            task_id=coordinates.get("task_id"),
            agent_name=coordinates.get("agent_name"),
            step_number=coordinates.get("step_number"),
            attempt=attempt,
            route_index=route_index,
        )
        accepted = await trace_manager.emit(event)
        if accepted and trace_manager.log_root:
            # Observational only: render from the immutable snapshot after it entered
            # the trace queue, and keep file I/O off the provider's hot path.
            try:
                from agentevolver.visual.request_viewer import request_log_root, schedule_request_html

                schedule_request_html(event, request_log_root(trace_manager.log_root))
            except Exception as render_error:  # noqa: BLE001 - never affect dispatch
                logger.debug(f"| model request HTML was not scheduled: {render_error}")
    except Exception as trace_error:  # noqa: BLE001 - integrity policy settles failure
        from agentevolver.trace.checkpoint import (
            TraceCheckpointBoundary,
            report_trace_integrity_failure,
        )

        await report_trace_integrity_failure(
            session_id,
            TraceCheckpointBoundary.MODEL_REQUEST,
            trace_error,
            profile=profile,
            metadata={"requested_model": requested_model, "routed_model": routed_model},
        )
        logger.debug(f"| model request snapshot not recorded: {trace_error}")
        return None
    from agentevolver.trace.checkpoint import TraceCheckpointBoundary, checkpoint_trace

    await checkpoint_trace(
        session_id,
        TraceCheckpointBoundary.MODEL_REQUEST,
        profile=profile,
        metadata={
            "requested_model": requested_model,
            "routed_model": routed_model,
            "request_snapshot_id": snapshot.snapshot_id,
        },
    )
    return snapshot.snapshot_id


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
            .register("google", "GOOGLE_API_KEY", "GOOGLE_API_BASE", "")
            .register("llm_hub", "LLM_HUB_API_KEY", "LLM_HUB_API_BASE", "")
        )
        await self._initialize_openai_models()
        await self._initialize_openrouter_models()
        await self._initialize_llm_hub_models()
        await self._initialize_anthropic_models()
        await self._initialize_google_models()
        logger.info(
            f"| Model context manager initialized with {len(self.models)} models."
        )

    # ------------------------------------------------------------------
    # Provider initialization
    # ------------------------------------------------------------------

    async def _initialize_openai_models(self):
        from agentevolver.model.config import openai_models
        specs = openai_models(
            max_tokens=self.max_tokens,
            default_temperature=self.default_temperature,
            default_reasoning=self.default_reasoning,
        )
        chat_models = specs["chat"]
        response_models = specs["response"]
        transcribe_models = specs["transcribe"]
        embedding_models = specs["embedding"]

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
                context_window=m.get("context_window"),
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
                context_window=m.get("context_window"),
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
                context_window=m.get("context_window"),
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
                context_window=m.get("context_window"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    async def _initialize_openrouter_models(self):
        from agentevolver.model.config import openrouter_models
        specs = openrouter_models(
            max_tokens=self.max_tokens,
            default_temperature=self.default_temperature,
            default_timeout=self.default_timeout,
            default_plugins=self.default_plugins,
            default_reasoning=self.default_reasoning,
        )
        chat_models = specs["chat"]

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
                context_window=m.get("context_window"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    async def _initialize_llm_hub_models(self):
        from agentevolver.model.config import llm_hub_models
        specs = llm_hub_models(
            max_tokens=self.max_tokens,
            default_temperature=self.default_temperature,
            default_timeout=self.default_timeout,
        )
        api_base = await self._key_pool.get_base("llm_hub")
        api_key = await self._key_pool.get_key("llm_hub")
        if not api_key:
            # Optional provider: without credentials its two models simply are not
            # registered, rather than every run logging a failure for a relay the
            # deployment may not use.
            return

        for m in specs["chat"]:
            cfg = ModelConfig(
                model_name=m["model_name"], model_id=m["model_id"], model_type=m["model_type"],
                provider="llm_hub", key_pool_name="llm_hub",
                api_base=api_base, api_key=api_key,
                temperature=m.get("temperature"),
                # Read from the catalog with a None fallback — NOT ModelConfig's default of
                # `{"reasoning_effort": "high"}`, which the relay silently ignores (it wants
                # `thinking.type.adaptive` + `output_config.effort`; an OpenAI-style
                # `reasoning_effort` returns 200 with no thinking at all). A chat entry that
                # sets `reasoning` gets exactly that shape as `extra_body`; one that omits it
                # (e.g. deepseek, a plain chat model) sends none.
                reasoning=m.get("reasoning") or None,
                max_completion_tokens=m.get("max_completion_tokens"),
                timeout=m.get("timeout", self.default_timeout),
                supports_streaming=True, supports_functions=True, supports_vision=True,
                output_version=None, fallback_model=m.get("fallback_model"),
                context_window=m.get("context_window"), max_retries=m.get("max_retries"),
                # Per-token prices so a call this relay does not price gets a computed cost.
                cost=m.get("cost"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

        for m in specs["response"]:
            cfg = ModelConfig(
                model_name=m["model_name"], model_id=m["model_id"], model_type=m["model_type"],
                provider="llm_hub", key_pool_name="llm_hub",
                api_base=api_base, api_key=api_key,
                reasoning=m.get("reasoning") or None,
                max_output_tokens=m.get("max_output_tokens"),
                timeout=m.get("timeout", self.default_timeout),
                # No token stream of its own; `stream()` buffers one call and replays it
                # as canonical events. Functions are the reason this surface is used.
                supports_streaming=False, supports_functions=True, supports_vision=True,
                output_version=None, fallback_model=m.get("fallback_model"),
                context_window=m.get("context_window"), max_retries=m.get("max_retries"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    async def _initialize_anthropic_models(self):
        from agentevolver.model.config import anthropic_models
        specs = anthropic_models(
            max_tokens=self.max_tokens,
            default_temperature=self.default_temperature,
            default_timeout=self.default_timeout,
            default_plugins=self.default_plugins,
            default_reasoning=self.default_reasoning,
        )
        chat_models = specs["chat"]

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
                context_window=m.get("context_window"),
            )
            self.models[cfg.model_name] = cfg
            await self._create_client(cfg)

    async def _initialize_google_models(self):
        _r = lambda: {"reasoning": {"enabled": True}}
        from agentevolver.model.config import google_models
        specs = google_models(max_tokens=self.max_tokens, default_temperature=self.default_temperature, default_timeout=self.default_timeout, default_plugins=self.default_plugins, default_reasoning=self.default_reasoning)
        chat_models = specs["chat"]

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
                context_window=m.get("context_window"),
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
        if config.provider == "openrouter":
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
        elif config.provider == "llm_hub":
            if config.model_type == "chat/completions":
                return ChatLLMHub(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    # Forwarded like the OpenRouter/Anthropic/Google branches — this one
                    # silently dropped it, so a catalog entry's `reasoning` never reached
                    # the relay and the model ran at its default effort. ChatLLMHub sends
                    # it through as `extra_body` (see _build_params).
                    reasoning=config.reasoning or None,
                    # Passed through as-is, like the Anthropic branch: a catalog entry
                    # that omits `temperature` means the model rejects it, and `or
                    # default` would put it back.
                    temperature=config.temperature,
                    max_completion_tokens=config.max_completion_tokens or self.max_tokens,
                )
            if config.model_type == "responses":
                return ResponseLLMHub(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    reasoning=config.reasoning or None,
                    max_output_tokens=config.max_output_tokens or self.max_tokens,
                    timeout=config.timeout or self.default_timeout,
                )
            raise ValueError(
                f"Unsupported model type {config.model_type} for LLM Hub provider"
            )
        elif config.provider == "anthropic":
            if config.model_type == "chat/completions":
                return ChatAnthropic(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base,
                    reasoning=config.reasoning or None,
                    # Passed through as-is: newer models (opus-4.8, fable-5) reject
                    # `temperature` outright, and their catalog entries omit it so the
                    # parameter is left off the request rather than defaulted to 0.7.
                    temperature=config.temperature,
                    max_tokens=config.max_completion_tokens or self.max_tokens,
                )
            raise ValueError(
                f"Unsupported model type {config.model_type} for Anthropic provider"
            )
        elif config.provider == "google":
            if config.model_type == "chat/completions":
                return ChatGoogle(
                    model=config.model_id,
                    api_key=config.api_key,
                    base_url=config.api_base or None,
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
        ]:
            raise ValueError(f"Unsupported provider: {config.provider}")
        self.models[config.model_name] = config
        await self._create_client(config)
        logger.info(f"Registered model: {config.model_name}")

    async def unregister_model(self, model_name: str) -> bool:
        """Remove a runtime model registration and its cached client."""
        existed = model_name in self.models
        self.models.pop(model_name, None)
        self.model_clients.pop(model_name, None)
        if existed:
            logger.info(f"Unregistered model: {model_name}")
        return existed

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_model_config(self, model: str) -> Optional[ModelConfig]:
        return self.models.get(model)

    def list(self) -> List[str]:
        return list(self.models.keys())

    async def compact_history(
        self,
        name: str,
        messages: List[Message],
        *,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        step_number: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return a provider-native checkpoint when the selected route supports it.

        Chat providers deliberately return ``None`` and continue through the portable
        text checkpoint path.  Native compaction is an optimization of the same logical
        window, never a requirement for an agent to keep running.
        """
        config = self.models.get(name)
        if config is None or config.model_type != "responses":
            return None
        client = await self._get_client(name)
        compact = getattr(client, "compact_history", None)
        if compact is None:
            return None
        snapshot_id = await _record_request_snapshot(
            session_id=session_id,
            requested_model=name,
            routed_model=name,
            model_config=config,
            client=client,
            messages=messages,
            tools=None,
            response_format=None,
            request_input={
                "operation": "compact",
                "trace_context": {
                    "task_id": task_id,
                    "agent_name": agent_name,
                    "step_number": step_number,
                },
            },
            call_kwargs={"operation": "compact"},
            stream=False,
            attempt=1,
            route_index=0,
        )
        result = await compact(messages)
        answer = {
            **(result or {}),
            "model": name,
            "provider": config.provider,
        }
        # The native endpoint has no AGENT_CALL event to refresh its page later. Attach
        # its own usage now; this keeps compaction cost distinct from the next generation.
        if session_id and snapshot_id and answer.get("usage"):
            try:
                from agentevolver.trace import trace_manager
                from agentevolver.trace.types import TraceEventType
                from agentevolver.visual.request_viewer import (
                    request_log_root,
                    schedule_request_html,
                )

                requests = [
                    event for event in trace_manager.events(session_id)
                    if event.event_type is TraceEventType.MODEL_REQUEST
                    and event.agent_name == agent_name
                ]
                current = next((
                    event for event in reversed(requests)
                    if (event.metadata or {}).get("request_snapshot_id") == snapshot_id
                ), None)
                if current is not None and trace_manager.log_root:
                    previous = next(
                        (event for event in reversed(requests) if event.seq_no < current.seq_no),
                        None,
                    )
                    schedule_request_html(
                        current,
                        request_log_root(trace_manager.log_root),
                        usage=answer["usage"],
                        previous_event=previous,
                    )
            except Exception as render_error:  # observational only
                logger.debug(f"| native compaction HTML was not refreshed: {render_error}")
        return answer

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------

    def _price_result(self, model_name: str, result: Response) -> None:
        """Fill in a computed `cost` on a buffered result's usage when the provider gave none.

        Patches both the structured `result.usage.cost` and the raw `result.data['usage']`
        dict (what the trace records), using the model's per-token price table. A provider
        that already returned a cost is left untouched. No-op when the model has no price
        table or the call had no usage.
        """
        if not result.success:
            return
        config = self.models.get(model_name)
        pricing = getattr(config, "cost", None) if config else None
        if not pricing:
            return
        from agentevolver.model.types import compute_cost
        raw = (result.data or {}).get("usage") if result.data else None
        if isinstance(raw, dict) and raw.get("cost") is None:
            from agentevolver.model.types import TokenUsage
            normalised = TokenUsage.from_raw(raw)
            if normalised is not None:
                raw["cost"] = compute_cost(normalised.model_dump(), pricing)
        if result.usage is not None and result.usage.cost is None:
            result.usage.cost = compute_cost(result.usage.model_dump(), pricing)

    def _log_usage(self, model_name: str, result: Response) -> None:
        if not result.success:
            return
        # Prefer the structured TokenUsage field; fall back to raw dict for older code paths
        usage = result.usage
        if usage is None and result.data:
            from agentevolver.model.types import TokenUsage

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
            ctx:   Optional ModelContext (carries id, name, workspace_root, timeout, extra).
        """
        import time as _t
        import httpx

        # ``from_context(None)`` creates an id for local bookkeeping. It must not turn a
        # health check with no session into a durable session of its own, so provenance
        # uses the caller-supplied id captured before conversion.
        session_id = getattr(ctx, "id", None)
        ctx = ModelContext.from_context(ctx)
        if not ctx.name:
            ctx = ctx.model_copy(update={"name": name})

        messages = input.get("messages", [])
        tools = input.get("tools")
        response_format = input.get("response_format")
        stream = input.get("stream", False)
        plugins = input.get("plugins")
        # Resolved against model_config below (a flaky route can raise its own ceiling); a
        # per-call value still wins.
        max_retries = input.get("max_retries")
        caller = input.get("caller")

        self._current_caller = caller
        # tools + response_format may be used together: the tool schemas constrain
        # tool-call arguments; response_format constrains the final answer. A turn
        # resolves to one or the other (provider serializers handle both).

        if name not in self.model_clients:
            return Response(
                type=ResponseType.LLM,
                success=False,
                message=f"Model {name} not found. Available: {list(self.models.keys())}",
            )

        model_config = self.models.get(name)
        max_retries = _resolve_max_retries(max_retries, model_config)
        last_exc: Exception = None
        try:
            primary_request = _prepare_request_messages(
                messages=messages,
                tools=tools,
                response_format=response_format,
                model_config=model_config,
                request_input=input,
                call_kwargs=kwargs,
                default_output_tokens=self.max_tokens,
                model_name=name,
            )
        except ContextOverflowError as overflow:
            # Skipping straight to the fallback, without spending an attempt. Sending it
            # anyway would cost `max_retries` identical rejections and their backoff, and
            # would report a context that cannot fit as a provider failure.
            logger.error(f"| ❌ {overflow}")
            primary_request, last_exc = None, overflow

        for attempt in range(max_retries if primary_request is not None else 0):
            _start = _t.time()
            try:
                client = await self._get_client(name)
                await _record_request_snapshot(
                    session_id=session_id,
                    requested_model=name,
                    routed_model=name,
                    model_config=model_config,
                    client=client,
                    messages=primary_request.messages,
                    tools=tools,
                    response_format=response_format,
                    request_input=input,
                    call_kwargs=kwargs,
                    stream=stream,
                    attempt=attempt + 1,
                    route_index=0,
                    pressure=primary_request.pressure,
                )
                result = await self._call_client(
                    client,
                    model_config,
                    primary_request.messages,
                    tools,
                    response_format,
                    stream,
                    plugins,
                    kwargs,
                )
                self._price_result(name, result)
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
                from agentevolver.trace.checkpoint import TraceIntegrityError
                if isinstance(e, TraceIntegrityError):
                    # Retrying or falling back cannot repair a missing source fact, and
                    # must never turn a fail-closed profile into another provider route.
                    raise
                # The provider stated its own limit. Retrying sends the identical
                # request into the identical rejection; the answer is a smaller
                # conversation, which only the caller can produce. Re-typed so the
                # caller reads it as the recoverable overflow it is.
                if provider_rejected_for_length(e):
                    last_exc = ContextOverflowError(
                        f"{name} rejected the request as too long for its context "
                        f"window: {e}"
                    )
                    logger.error(f"| ❌ {last_exc}")
                    break
                last_exc = e
                _elapsed = _t.time() - _start
                tag = f", caller={self._current_caller}" if self._current_caller else ""
                more = attempt < max_retries - 1
                # Computed before the record so the trace says how long the wait will be,
                # not merely that there was one. An empty completion is not a rate limit:
                # it is a transient upstream blip (llm_hub's Bedrock-backed opus route
                # returns one intermittently) that the very next call almost always
                # answers, so it gets a short flat wait instead of the exponential backoff
                # meant for rate limits and half-open connections. On one run 84 empties
                # spent 6.3 min in exponential backoff that bought nothing; a rate limit or
                # a dropped stream still gets the long climb, because there retrying sooner
                # only re-triggers the same rejection.
                if more:
                    delay = (_EMPTY_COMPLETION_RETRY_DELAY if _is_transient_empty(e)
                             else _retry_delay(attempt + 1))
                else:
                    delay = None
                await _record_retry(
                    session_id, name, attempt + 1, max_retries,
                    str(e), delay, self._current_caller,
                )
                if more:
                    logger.warning(
                        f"| ⚠️ Model {name} attempt {attempt+1}/{max_retries} failed ({_elapsed:.0f}s{tag}): {e}, "
                        f"retrying in {delay:.1f}s..."
                    )
                    # Backing off is the whole reason a retry helps. Retrying instantly
                    # re-sends into the same rate limit or the same half-open connection,
                    # which is why the previous loop's three attempts so often failed three
                    # times for one reason.
                    await asyncio.sleep(delay)
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
                fallback_request = _prepare_request_messages(
                    messages=messages,
                    tools=tools,
                    response_format=response_format,
                    model_config=fallback_config,
                    request_input=input,
                    call_kwargs=kwargs,
                    default_output_tokens=self.max_tokens,
                    model_name=fallback,
                )
            except ContextOverflowError as overflow:
                return Response(
                    type=ResponseType.LLM, success=False, message=str(overflow),
                    data={"pressure": overflow.pressure},
                )
            try:
                fb_client = await self._get_client(fallback)
                await _record_request_snapshot(
                    session_id=session_id,
                    requested_model=name,
                    routed_model=fallback,
                    model_config=fallback_config,
                    client=fb_client,
                    messages=fallback_request.messages,
                    tools=tools,
                    response_format=response_format,
                    request_input=input,
                    call_kwargs=kwargs,
                    stream=stream,
                    attempt=1,
                    route_index=1,
                    pressure=fallback_request.pressure,
                )
                result = await self._call_client(
                    fb_client,
                    fallback_config,
                    fallback_request.messages,
                    tools,
                    response_format,
                    stream,
                    plugins,
                    kwargs,
                )
                self._price_result(fallback, result)
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
                from agentevolver.trace.checkpoint import TraceIntegrityError
                if isinstance(fallback_error, TraceIntegrityError):
                    raise
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
        ``agentevolver.model.types``).

        Retry/fallback are applied ONLY before the first event is emitted: if the
        upstream fails while opening the stream (transient 5xx / timeout), we retry
        the same model up to ``max_retries`` times and then fall back to
        ``fallback_model``. Once any event has been yielded downstream we can no
        longer restart safely (it would duplicate output), so a mid-stream failure
        propagates to the caller.
        """
        import httpx
        from agentevolver.model.types import buffered_response_to_events

        session_id = getattr(ctx, "id", None)
        ctx = ModelContext.from_context(ctx)
        messages = input.get("messages", [])
        tools = input.get("tools")
        response_format = input.get("response_format")
        # Resolved against model_config below (a flaky route can raise its own ceiling); a
        # per-call value still wins.
        max_retries = input.get("max_retries")

        if name not in self.model_clients:
            raise ValueError(f"Model {name} not found. Available: {list(self.models.keys())}")

        from agentevolver.model.types import StreamDone as _StreamDone, compute_cost as _compute_cost

        def _price_event(target: str, ev: Any) -> Any:
            """Fill a computed cost onto a StreamDone's usage when the provider gave none.

            The streaming path's only usage is on StreamDone; pricing it here is what puts a
            dollar figure on every streamed call in the trace (the agent loop reads this same
            usage). Left untouched when the model has no price table or usage already has a
            cost."""
            if not isinstance(ev, _StreamDone) or not isinstance(ev.usage, dict):
                return ev
            if ev.usage.get("cost") is not None:
                return ev
            cfg = self.models.get(target)
            pricing = getattr(cfg, "cost", None) if cfg else None
            if pricing:
                from agentevolver.model.types import TokenUsage
                normalised = TokenUsage.from_raw(ev.usage)
                if normalised is not None:
                    ev.usage["cost"] = _compute_cost(normalised.model_dump(), pricing)
            return ev

        async def _events(target: str, client: Any, effective_messages: List[Any]):
            """Canonical events for one model (true stream, or buffered→events)."""
            if hasattr(client, "stream"):
                async for ev in client.stream(
                    messages=effective_messages, tools=tools, response_format=response_format, **kwargs
                ):
                    yield _price_event(target, ev)
            else:
                # Providers without a stream(): buffer one call, re-emit as events.
                resp = await client(
                    messages=effective_messages, tools=tools, response_format=response_format, **kwargs
                )
                async for ev in buffered_response_to_events(resp):
                    yield _price_event(target, ev)

        model_config = self.models.get(name)
        max_retries = _resolve_max_retries(max_retries, model_config)

        # Ordered attempt plan: primary (retried max_retries×) then fallback (once).
        plan: List[tuple] = [(name, max_retries)]
        fb = model_config.fallback_model if model_config else None
        if fb and fb != name and fb in self.model_clients:
            plan.append((fb, 1))

        last_exc: Optional[Exception] = None
        for ci, (target, attempts) in enumerate(plan):
            for attempt in range(attempts):
                started = False
                try:
                    client = await self._get_client(target)
                    effective = _prepare_request_messages(
                        messages=messages,
                        tools=tools,
                        response_format=response_format,
                        model_config=self.models.get(target),
                        request_input=input,
                        call_kwargs=kwargs,
                        model_name=target,
                        default_output_tokens=self.max_tokens,
                    )
                    await _record_request_snapshot(
                        session_id=session_id,
                        requested_model=name,
                        routed_model=target,
                        model_config=self.models.get(target),
                        client=client,
                        messages=effective.messages,
                        tools=tools,
                        response_format=response_format,
                        request_input=input,
                        call_kwargs=kwargs,
                        stream=True,
                        attempt=attempt + 1,
                        route_index=ci,
                        pressure=effective.pressure,
                    )
                    async for ev in _events(target, client, effective.messages):
                        started = True
                        yield ev
                    return  # stream completed cleanly
                except ContextOverflowError as overflow:
                    # Not a failed attempt: the request was never sent, and re-sending it
                    # to this same model would be rejected identically every time. Its
                    # remaining attempts are given up so the next model in the plan — which
                    # may have a larger window — gets its turn immediately.
                    logger.error(f"| ❌ {overflow}")
                    last_exc = overflow
                    break
                except Exception as e:
                    from agentevolver.trace.checkpoint import TraceIntegrityError
                    if isinstance(e, TraceIntegrityError):
                        raise
                    # Same reasoning as the branch above: the provider's own limit is
                    # the authority, and it just stated it. Give up this candidate's
                    # remaining attempts so the next model — which may have a larger
                    # window — gets its turn, and let the caller fold history.
                    if not started and provider_rejected_for_length(e):
                        last_exc = ContextOverflowError(
                            f"{target} rejected the request as too long for its context "
                            f"window: {e}"
                        )
                        logger.error(f"| ❌ {last_exc}")
                        break
                    last_exc = e
                    if started:
                        # Already emitted output downstream — restarting would
                        # duplicate it, so surface the error instead.
                        logger.error(
                            f"| ❌ Stream {target} failed mid-stream ({type(e).__name__}); "
                            f"cannot retry: {e}"
                        )
                        raise
                    logger.warning(
                        f"| ⚠️ Stream {target} failed before first event "
                        f"(attempt {attempt+1}/{attempts}, {type(e).__name__}): {e}"
                    )
            if ci < len(plan) - 1:
                logger.warning(
                    f"| Stream {target} exhausted retries, falling back to {plan[ci+1][0]}"
                )

        # Every candidate failed before emitting anything.
        raise last_exc if last_exc else RuntimeError(f"Stream {name} failed to start")


__all__ = ["ApiKeyPool", "ModelContextManager"]
