from typing import Any, Optional, Union, List, Dict, ClassVar
import httpx

try:
    import google.generativeai as genai
    from google.generativeai.types import HarmCategory, HarmBlockThreshold
    from google.api_core import exceptions as google_exceptions
except ImportError:
    genai = None
    HarmCategory = None
    HarmBlockThreshold = None
    google_exceptions = None

from pydantic import BaseModel, Field, ConfigDict

import json
from src.logger import logger
from src.response.types import Response, ResponseType
from src.model.types import TokenUsage, BaseChatModel
from src.message.types import Message, HumanMessage, SystemMessage, AssistantMessage
from src.model.google.serializer import GoogleChatSerializer
from typing import Type, TYPE_CHECKING

if TYPE_CHECKING:
    from src.tool.types import Tool

class ChatGoogle(BaseChatModel):
    """
    A wrapper that provides a unified interface for Google Gemini chat completions.
    
    This class handles Google Gemini API-specific formatting and provides methods for chat completions
    with support for tools and streaming.
    
    Note: Google Gemini uses response_schema for structured outputs.
    """
    
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    supports_true_streaming: bool = True

    # Model configuration
    model: str

    # Model params
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    max_output_tokens: Optional[int] = 8192
    reasoning: Optional[Dict[str, Any]] = None
    
    # Client initialization parameters
    api_key: Optional[str] = None
    timeout: Optional[Union[float, httpx.Timeout]] = httpx.Timeout(600.0, connect=30.0)
    max_retries: int = 5

    @property
    def provider(self) -> str:
        return 'google'

    def set_api_key(self, api_key: str) -> None:
        self.api_key = api_key

    def _get_client_params(self) -> Dict[str, Any]:
        """Prepare client parameters dictionary."""
        if genai is None:
            raise ImportError("google-generativeai package is required. Install it with: pip install google-generativeai")
        
        # Configure API key
        if self.api_key:
            genai.configure(api_key=self.api_key)
        elif not genai.api_key:
            # Try to get from environment
            import os
            api_key = os.getenv("GOOGLE_API_KEY")
            if api_key:
                genai.configure(api_key=api_key)
            else:
                raise ValueError("Google API key is required. Set GOOGLE_API_KEY environment variable or pass api_key parameter.")
        
        return {}

    def get_client(self, system_instruction: Optional[str] = None):
        """
        Returns a Google GenerativeModel instance.

        Args:
            system_instruction: Optional system instruction to configure the model
        
        Returns:
            GenerativeModel: An instance of the GenerativeModel client.
        """
        if genai is None:
            raise ImportError("google-generativeai package is required. Install it with: pip install google-generativeai")
        
        self._get_client_params()
        
        # system_instruction should be passed when creating the model, not in generate_content
        if system_instruction:
            return genai.GenerativeModel(
                self.model,
                system_instruction=system_instruction
            )
        else:
            return genai.GenerativeModel(self.model)

    @property
    def name(self) -> str:
        return str(self.model)

    @staticmethod
    def _usage_dict(u) -> Optional[Dict[str, Any]]:
        """Gemini's ``usage_metadata`` is a protobuf message (no ``model_dump`` / not
        dict-iterable). Pull its token counts into a plain dict — ``TokenUsage.from_raw``
        already understands these ``*_token_count`` keys."""
        if u is None:
            return None
        return {
            "prompt_token_count": getattr(u, "prompt_token_count", 0) or 0,
            "candidates_token_count": getattr(u, "candidates_token_count", 0) or 0,
            "total_token_count": getattr(u, "total_token_count", 0) or 0,
        }

    def _get_usage(self, response) -> Optional[Dict[str, Any]]:
        """Extract usage information from Google Gemini response."""
        if hasattr(response, 'usage_metadata') and response.usage_metadata is not None:
            return self._usage_dict(response.usage_metadata)
        else:
            return None

    async def _build_params(
        self,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        response_format: Optional[Union[Type[BaseModel], BaseModel, Dict]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Build parameters for API call.
        
        Step 1: Convert messages, tools, and response_format into API-ready parameters.
        
        Args:
            messages: List of Message objects
            tools: Optional list of Tool instances
            response_format: Optional response format (Pydantic model class, instance or dict)
            stream: Whether to stream the response
            **kwargs: Additional parameters
            
        Returns:
            Dictionary containing:
            - system_instruction: System instruction (if any)
            - contents: Serialized messages
            - generation_config: Generation configuration (temperature, max_output_tokens, etc.)
            - tools: Serialized tools (if any)
            - params: All other API parameters
        """
        # Serialize messages to Google Gemini format
        system_instruction, gemini_contents = GoogleChatSerializer.serialize_messages(messages)
        
        # Build generation config
        generation_config: Dict[str, Any] = {}
        
        if self.temperature is not None:
            generation_config['temperature'] = self.temperature
        if self.top_p is not None:
            generation_config['top_p'] = self.top_p
        if self.top_k is not None:
            generation_config['top_k'] = self.top_k
        if self.max_output_tokens is not None:
            generation_config['max_output_tokens'] = self.max_output_tokens
        # ``reasoning`` in the model spec is the OpenRouter-style toggle
        # ({"reasoning": {"enabled": ...}}) — Gemini's GenerationConfig has no such field
        # (thinking is on by default for 2.5+/3 and configured separately), so passing it
        # raises "Unknown field for GenerationConfig: reasoning". Only forward its
        # thinking_config if a caller supplied one in that shape.
        if self.reasoning:
            tc = self.reasoning.get("thinking_config")
            if tc is not None:
                generation_config["thinking_config"] = tc
        # Handle response_format (Google Gemini uses response_schema)
        if response_format:
            try:
                response_format_config = GoogleChatSerializer.serialize_response_format(response_format)
                generation_config.update(response_format_config)
            except ValueError as e:
                logger.warning(f"Failed to serialize response_format: {e}")
        
        # Format tools using serializer
        tools_config = None
        if tools:
            formatted_tools = GoogleChatSerializer.serialize_tools(tools)
            if formatted_tools:
                tools_config = formatted_tools
        
        # Merge additional kwargs into generation_config
        for key, value in kwargs.items():
            if key not in ['contents', 'system_instruction', 'tools', 'generation_config']:
                generation_config[key] = value
        
        return {
            "system_instruction": system_instruction,
            "contents": gemini_contents,
            "generation_config": generation_config,
            "tools": tools_config,
            "stream": stream,
        }

    async def _call_model(self, built: Dict[str, Any]) -> Any:
        """Perform one non-streaming API call from a _build_params result.

        system_instruction goes to the model constructor, not generate_content.
        """
        client = self.get_client(system_instruction=built.get("system_instruction"))
        call_kwargs: Dict[str, Any] = {}
        if built.get("contents"):
            call_kwargs['contents'] = built["contents"]
        if built.get("generation_config"):
            call_kwargs['generation_config'] = built["generation_config"]
        if built.get("tools"):
            call_kwargs['tools'] = built["tools"]
        return await self._async_generate_content(client, **call_kwargs)

    async def _async_generate_content(self, client, **kwargs):
        """Async wrapper for Google Gemini generate_content."""
        import asyncio
        
        # Google Gemini SDK is synchronous, so we run it in a thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: client.generate_content(**kwargs))

    async def _open_stream(self, built: Dict[str, Any]):
        """Open Gemini's stream from a _build_params result.

        The google-generativeai SDK is synchronous, so its streaming generator is
        bridged to async via a background thread → asyncio.Queue, returned as an
        async iterator of raw chunks for _parse_stream.
        """
        import asyncio
        import threading

        if genai is None:
            raise ImportError("google-generativeai package is required. Install it with: pip install google-generativeai")
        client = self.get_client(system_instruction=built.get("system_instruction"))
        call_kwargs: Dict[str, Any] = {}
        if built.get("contents"):
            call_kwargs["contents"] = built["contents"]
        if built.get("generation_config"):
            call_kwargs["generation_config"] = built["generation_config"]
        if built.get("tools"):
            call_kwargs["tools"] = built["tools"]

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _produce():
            try:
                for chunk in client.generate_content(stream=True, **call_kwargs):
                    loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
            except Exception as exc:  # surface producer errors to the consumer
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))

        threading.Thread(target=_produce, daemon=True).start()

        async def _aiter():
            while True:
                kind, payload = await queue.get()
                if kind == "done":
                    break
                if kind == "error":
                    raise payload
                yield payload

        return _aiter()

    async def _parse_stream(self, raw):
        """Translate Gemini chunks → canonical stream events. Unit-testable."""
        from src.model.types import TextDelta, ToolCallComplete, StreamDone, normalize_stop_reason

        usage = None
        finish = None
        tool_index = 0
        async for chunk in raw:
            u = getattr(chunk, "usage_metadata", None)
            if u is not None:
                usage = self._usage_dict(u)
            cands = getattr(chunk, "candidates", None) or []
            if not cands:
                continue
            cand = cands[0]
            fr = getattr(cand, "finish_reason", None)
            if fr:
                finish = fr
            content = getattr(cand, "content", None)
            parts = (getattr(content, "parts", None) or []) if content else []
            for part in parts:
                txt = getattr(part, "text", None)
                if txt:
                    yield TextDelta(txt)
                fc = getattr(part, "function_call", None)
                name = (getattr(fc, "name", "") or "") if fc is not None else ""
                if name:  # a text part carries an empty function_call — skip it
                    args = getattr(fc, "args", {}) or {}
                    try:
                        args = dict(args)  # Gemini args may be a proto Map
                    except Exception:
                        pass
                    yield ToolCallComplete(index=tool_index, id=f"call_{tool_index}", name=name, input=args)
                    tool_index += 1
        fr_name = getattr(finish, "name", None) or (str(finish) if finish is not None else None)
        yield StreamDone(stop_reason=normalize_stop_reason(fr_name), usage=usage)

    async def _format_response(
        self,
        response: Any,
        tools: Optional[List["Tool"]] = None,
        response_format: Optional[Union[Type[BaseModel], BaseModel, Dict]] = None,
    ) -> Response:
        """Format Google Gemini response into Response."""
        try:
            # Handle SDK response object
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
            elif isinstance(response, dict):
                candidates = response.get("candidates", [])
                candidate = candidates[0] if candidates else {}
            else:
                candidate = {}

            if not candidate:
                return Response(
                    type=ResponseType.LLM,
                    success=False,
                    message="No candidates in response",
                    data={"raw_response": str(response)},
                )

            # Extract content and function calls
            text_parts = []
            function_calls = []
            
            if hasattr(candidate, 'content'):
                # SDK response object
                content = candidate.content
                if hasattr(content, 'parts'):
                    parts = content.parts
                else:
                    parts = []
            elif isinstance(candidate, dict):
                # Dict format
                content = candidate.get("content", {})
                parts = content.get("parts", [])
            else:
                parts = []
            
            for part in parts:
                # A Gemini Part protobuf always carries BOTH ``text`` and ``function_call``
                # attributes (empty when unused), so check each by VALUE, not hasattr — else
                # a function-call part (empty text) is misread as text and the call is lost.
                if isinstance(part, dict):
                    txt = part.get("text")
                    fc = part.get("function_call")
                else:
                    txt = getattr(part, "text", None)
                    fc = getattr(part, "function_call", None)
                if txt:
                    text_parts.append(txt)
                fc_name = (fc.get("name") if isinstance(fc, dict) else getattr(fc, "name", "")) if fc else ""
                if fc_name:
                    fc_args = (fc.get("args") if isinstance(fc, dict) else getattr(fc, "args", {})) or {}
                    try:
                        fc_args = dict(fc_args)  # Gemini args may be a proto Map
                    except Exception:
                        pass
                    function_calls.append({"name": fc_name, "args": fc_args})

            message_text = "\n".join(text_parts) if text_parts else ""

            usage = self._get_usage(response)
            finish_reason = None
            if hasattr(candidate, 'finish_reason'):
                finish_reason = candidate.finish_reason
            elif isinstance(candidate, dict):
                finish_reason = candidate.get("finish_reason")

            # Handle function calling
            if tools and function_calls:
                formatted_lines = []
                functions = []

                for func_call in function_calls:
                    name = func_call.get("name", "")
                    args_data = func_call.get("args", {})

                    # Format arguments as keyword arguments
                    if args_data:
                        args_str = ", ".join([f"{k}={v!r}" for k, v in args_data.items()])
                        formatted_lines.append(f"Calling function {name}({args_str})")
                    else:
                        formatted_lines.append(f"Calling function {name}()")

                    functions.append({
                        "name": name,
                        "args": args_data
                    })

                formatted_message = "\n".join(formatted_lines)


                return Response(
                    type=ResponseType.LLM,
                    success=True,
                    message=formatted_message,
                    data={
                        "raw_response": str(response),
                        "functions": functions,
                        "usage": usage,
                        "finish_reason": finish_reason,
                    },
                    usage=TokenUsage.from_raw(usage),
                )

            # Handle structured output (if response_format was provided)
            elif response_format and isinstance(response_format, type) and issubclass(response_format, BaseModel):
                if not message_text:
                    return Response(
                        type=ResponseType.LLM,
                        success=False,
                        message="Empty response content from model",
                        data={"raw_response": str(response)},
                    )

                # Try to parse JSON from message text
                import json
                try:
                    data = json.loads(message_text)
                    parsed_model = response_format.model_validate(data)

                    # Format as string
                    model_name = response_format.__name__
                    model_dict = parsed_model.model_dump()

                    field_lines = []
                    for field_name, field_value in model_dict.items():
                        field_lines.append(f"{field_name}={field_value!r}")

                    formatted_message = f"Response result:\n\n{model_name}(\n"
                    formatted_message += ",\n".join(f"    {line}" for line in field_lines)
                    formatted_message += "\n)"


                    return Response(
                        type=ResponseType.LLM,
                        success=True,
                        message=formatted_message,
                        data={
                            "raw_response": str(response),
                            "usage": usage,
                            "finish_reason": finish_reason,
                        },
                        usage=TokenUsage.from_raw(usage),
                        parsed_model=parsed_model,
                    )
                except json.JSONDecodeError as e:
                    return Response(
                        type=ResponseType.LLM,
                        success=False,
                        message=f"Failed to parse JSON from response: {e}",
                        data={"error": str(e), "content": message_text},
                    )
                except Exception as e:
                    return Response(
                        type=ResponseType.LLM,
                        success=False,
                        message=f"Failed to validate response against schema: {e}",
                        data={"error": str(e), "content": message_text},
                    )

            # Default: return content as string
            else:

                return Response(
                    type=ResponseType.LLM,
                    success=True,
                    message=message_text,
                    data={
                        "raw_response": str(response),
                        "usage": usage,
                        "finish_reason": finish_reason,
                    },
                    usage=TokenUsage.from_raw(usage),
                )

        except Exception as e:
            logger.error(f"Failed to format response: {e}")
            return Response(
                type=ResponseType.LLM,
                success=False,
                message=f"Failed to format response: {e}",
                data={"error": str(e)},
            )

