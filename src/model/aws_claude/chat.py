from collections.abc import Mapping
from typing import Any, Optional, Union, List, Dict, Type
import httpx
import dirtyjson

try:
    from openai.types.chat.chat_completion import ChatCompletion
except ImportError:
    # Fallback if openai package is not available
    ChatCompletion = dict

from pydantic import BaseModel, Field, ConfigDict

from src.logger import logger
from src.response.types import Response, ResponseType
from src.model.types import TokenUsage
from src.message.types import Message
from src.model.aws_claude.serializer import AwsClaudeChatSerializer
from src.model.aws_claude.rest import AwsClaudeClient
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tool.types import Tool


class ChatAwsClaude(BaseModel):
    """
    A wrapper that provides a unified interface for AWS Claude chat completions.

    The AWS Claude gateway exposes an OpenAI-compatible `/chat/completions`
    endpoint, so this class talks to it over plain HTTP (httpx) via
    `AwsClaudeClient`, mirroring the OpenRouter integration.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    # Model configuration
    model: str

    # Model params
    temperature: Optional[float] = 0.7
    frequency_penalty: Optional[float] = None
    reasoning: Optional[Dict[str, Any]] = None
    seed: Optional[int] = None
    top_p: Optional[float] = None
    max_completion_tokens: Optional[int] = 16384

    # Client initialization parameters
    api_key: Optional[str] = None
    base_url: Optional[Union[str, httpx.URL]] = None
    timeout: Optional[Union[float, httpx.Timeout]] = httpx.Timeout(600.0, connect=30.0)
    default_headers: Optional[Mapping[str, str]] = None
    http_client: Optional[httpx.AsyncClient] = None

    @property
    def provider(self) -> str:
        return 'aws_claude'

    def set_api_key(self, api_key: str) -> None:
        self.api_key = api_key

    def get_client(self) -> AwsClaudeClient:
        """Get the AWS Claude REST client."""
        return AwsClaudeClient(
            api_key=self.api_key,
            base_url=str(self.base_url) if self.base_url else None,
            default_headers=self.default_headers,
            timeout=self.timeout,
            http_client=self.http_client,
        )

    @property
    def name(self) -> str:
        return str(self.model)

    def _get_usage(self, response: ChatCompletion) -> Optional[Dict[str, Any]]:
        """Extract usage information from response."""
        if getattr(response, "usage", None) is not None:
            return response.usage.model_dump()
        else:
            return None

    async def _build_params(
        self,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        response_format: Optional[Union[BaseModel, Dict]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Build parameters for API call.

        Step 1: Convert messages, tools, and response_format into API-ready parameters.

        Returns:
            Dictionary containing:
            - messages: Serialized messages
            - params: All other API parameters (tools, response_format, stream, etc.)
        """
        # Serialize messages to OpenAI-compatible format
        aws_messages = AwsClaudeChatSerializer.serialize_messages(messages)

        # Build API parameters
        params: Dict[str, Any] = {}

        # Add model parameters
        if self.temperature is not None:
            params['temperature'] = self.temperature
        if self.frequency_penalty is not None:
            params['frequency_penalty'] = self.frequency_penalty
        if self.max_completion_tokens is not None:
            params['max_completion_tokens'] = self.max_completion_tokens
        if self.top_p is not None:
            params['top_p'] = self.top_p
        if self.seed is not None:
            params['seed'] = self.seed
        if self.reasoning is not None:
            # Drop OpenAI/OpenRouter-only `reasoning_effort` key; pass remaining
            # reasoning config through as extra body fields.
            extra = {k: v for k, v in self.reasoning.items() if k != "reasoning_effort"}
            if extra:
                params['extra_body'] = extra

        # Format tools using serializer
        if tools:
            formatted_tools = AwsClaudeChatSerializer.serialize_tools(tools)
            if formatted_tools:
                params['tools'] = formatted_tools

        # Handle response_format.
        if response_format:
            schema_model: Optional[Type[BaseModel]] = None
            if isinstance(response_format, type) and issubclass(response_format, BaseModel):
                schema_model = response_format
            elif isinstance(response_format, BaseModel):
                schema_model = type(response_format)

            if schema_model is not None:
                serialized = AwsClaudeChatSerializer.serialize_response_format(schema_model, model_name=self.model)
                schema = serialized["json_schema"]["schema"]
                tool_name = schema_model.__name__
                structured_tool = {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": f"Return the final answer strictly as a {tool_name} object.",
                        "parameters": schema,
                    },
                }
                params["tools"] = (params.get("tools") or []) + [structured_tool]
                params["tool_choice"] = {"type": "function", "function": {"name": tool_name}}
            elif isinstance(response_format, dict):
                params['response_format'] = response_format
            else:
                logger.warning(f"Unsupported response_format type: {type(response_format)}")

        # Handle streaming
        if stream:
            params['stream'] = True

        # Merge additional kwargs
        params.update(kwargs)

        return {
            "messages": aws_messages,
            "params": params,
        }

    async def _call_model(
        self,
        messages: List[Dict[str, Any]],
        **params: Any,
    ) -> ChatCompletion:
        """
        Call the model API (Step 2).

        Args:
            messages: Serialized messages
            **params: API parameters

        Returns:
            ChatCompletion object (compatible format)
        """
        import time as _t
        _start = _t.time()

        try:
            client = self.get_client()
            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                **params,
            )
        except Exception as e:
            _elapsed = _t.time() - _start
            logger.error(f"| 🔴 AWS Claude error ({_elapsed:.0f}s, model={self.model}): {type(e).__name__}: {e}")
            raise

        return response

    async def _format_response(
        self,
        response: ChatCompletion,
        tools: Optional[List["Tool"]] = None,
        response_format: Optional[Union[BaseModel, Dict]] = None,
    ) -> Response:
        """Format AWS Claude response into Response."""
        try:
            if not response.choices:
                return Response(
                    type=ResponseType.LLM,
                    success=False,
                    message="No choices in response",
                    data={"raw_response": response.model_dump() if hasattr(response, 'model_dump') else str(response)},
                )

            message = response.choices[0].message
            usage = self._get_usage(response)
            finish_reason = response.choices[0].finish_reason

            # Handle structured output FIRST.
            #
            # When `response_format` is a Pydantic model we forced a synthetic
            # tool call (see `_build_params`), so the structured result lives in
            # the tool call arguments — NOT in `message.content`. Parse it from
            # there, falling back to content for older/looser gateway behavior.
            if response_format and isinstance(response_format, type) and issubclass(response_format, BaseModel):
                tool_name = response_format.__name__
                raw: Optional[Any] = None
                if message.tool_calls:
                    for tool_call in message.tool_calls:
                        if tool_call.function.name == tool_name:
                            raw = tool_call.function.arguments
                            break
                    if raw is None:
                        raw = message.tool_calls[0].function.arguments
                if raw is None:
                    raw = message.content or ""

                if not raw:
                    return Response(
                        type=ResponseType.LLM,
                        success=False,
                        message="Empty structured output from model",
                        data={"raw_response": response.model_dump() if hasattr(response, 'model_dump') else str(response)},
                    )

                try:
                    data = dirtyjson.loads(raw) if isinstance(raw, str) else raw
                    parsed_model = response_format.model_validate(data)

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
                            "raw_response": response.model_dump() if hasattr(response, 'model_dump') else str(response),
                            "usage": usage,
                            "finish_reason": finish_reason,
                        },
                        usage=TokenUsage.from_raw(usage),
                        parsed_model=parsed_model,
                    )
                except (dirtyjson.Error, ValueError, TypeError) as e:
                    return Response(
                        type=ResponseType.LLM,
                        success=False,
                        message=f"Failed to parse JSON from response: {e}",
                        data={"error": str(e), "content": raw},
                    )
                except Exception as e:
                    return Response(
                        type=ResponseType.LLM,
                        success=False,
                        message=f"Failed to validate response against schema: {e}",
                        data={"error": str(e), "content": raw},
                    )

            # Handle function calling
            elif tools and message.tool_calls:
                formatted_lines = []
                functions = []

                for tool_call in message.tool_calls:
                    function_info = tool_call.function
                    name = function_info.name
                    arguments_str = function_info.arguments

                    try:
                        arguments = dirtyjson.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
                    except (dirtyjson.Error, ValueError, TypeError):
                        arguments = {}

                    if arguments:
                        args_str = ", ".join([f"{k}={v!r}" for k, v in arguments.items()])
                        formatted_lines.append(f"Calling function {name}({args_str})")
                    else:
                        formatted_lines.append(f"Calling function {name}()")

                    functions.append({
                        "name": name,
                        "arguments": arguments
                    })

                formatted_message = "\n".join(formatted_lines)


                return Response(
                    type=ResponseType.LLM,
                    success=True,
                    message=formatted_message,
                    data={
                        "raw_response": response.model_dump() if hasattr(response, 'model_dump') else str(response),
                        "functions": functions,
                        "usage": usage,
                        "finish_reason": finish_reason,
                    },
                    usage=TokenUsage.from_raw(usage),
                )

            # Default: return content as string
            else:
                content = message.content or ""


                return Response(
                    type=ResponseType.LLM,
                    success=True,
                    message=content,
                    data={
                        "raw_response": response.model_dump() if hasattr(response, 'model_dump') else str(response),
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

    async def __call__(
        self,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        response_format: Optional[Union[BaseModel, Dict]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Response:
        """
        Execute asynchronous completion call via the AWS Claude API.

        Args:
            messages: List of Message objects (HumanMessage, SystemMessage, AssistantMessage)
            tools: Optional list of Tool instances
            response_format: Optional response format (Pydantic model or dict)
            stream: Whether to stream the response
            **kwargs: Additional parameters

        Returns:
            Response with formatted message
        """
        try:
            # Step 1: Build parameters
            params = await self._build_params(
                messages=messages,
                tools=tools,
                response_format=response_format,
                stream=stream,
                **kwargs,
            )

            # Step 2: Call model API
            response = await self._call_model(
                messages=params["messages"],
                **params["params"],
            )

            # Step 3: Format response
            return await self._format_response(
                response=response,
                tools=tools,
                response_format=response_format,
            )

        except httpx.TimeoutException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return Response(
                type=ResponseType.LLM,
                success=False,
                message=f"Unexpected error: {str(e)}",
                data={"error": str(e), "model": self.name},
            )
