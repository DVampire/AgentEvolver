from typing import Any, Dict, List, Optional, Type, Union

import dirtyjson
from pydantic import BaseModel, ValidationError

from src.logger import logger
from src.response.types import Response, ResponseType
from src.model.types import TokenUsage
from src.message.types import Message
from src.model.anthropic.chat import ChatAnthropic
from src.model.aws_claude.serializer import AwsClaudeChatSerializer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tool.types import Tool


class ChatAwsClaudeNative(ChatAnthropic):
    """
    Anthropic-native /v1/messages client for the AWS Claude gateway.

    The aws_claude gateway forwards `thinking` / `output_config` verbatim on
    /v1/messages, so adaptive thinking works on this route — unlike the
    OpenAI-compatible /chat/completions route (ChatAwsClaude), where the
    gateway silently drops both fields and maps `reasoning_effort` to the
    legacy `thinking.enabled` shape, which Opus 4.7+ rejects with a 400.

    Two gateway/model quirks shape the structured-output handling:
    - The gateway strips the structured-outputs `output_format` param, so
      structured output uses a synthetic tool instead (same approach as
      ChatAwsClaude).
    - A forced `tool_choice` ({"type": "tool"}) silently disables thinking,
      so the synthetic tool is requested with `tool_choice: auto` plus an
      explicit system instruction, and the response parser falls back to the
      text content if the model answered without calling the tool.
    """

    @property
    def provider(self) -> str:
        return "aws_claude"

    def _get_client_params(self) -> Dict[str, Any]:
        params = super()._get_client_params()
        # The anthropic SDK appends /v1/... to base_url itself; the gateway
        # base from AWS_CLAUDE_API_BASE already ends with /v1 — strip it to
        # avoid /v1/v1/messages.
        if params.get("base_url"):
            base = str(params["base_url"]).rstrip("/")
            if base.endswith("/v1"):
                base = base[: -len("/v1")]
            params["base_url"] = base
        return params

    @staticmethod
    def _schema_model(response_format: Optional[Union[Type[BaseModel], BaseModel, Dict]]) -> Optional[Type[BaseModel]]:
        if isinstance(response_format, type) and issubclass(response_format, BaseModel):
            return response_format
        if isinstance(response_format, BaseModel):
            return type(response_format)
        return None

    async def _build_params(
        self,
        messages: List[Message],
        tools: Optional[List["Tool"]] = None,
        response_format: Optional[Union[Type[BaseModel], BaseModel, Dict]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        schema_model = self._schema_model(response_format)
        if response_format is not None and schema_model is None:
            logger.warning(f"Unsupported response_format type for aws_claude native API: {type(response_format)}")

        # Let the parent build everything except structured output (its
        # output_format path is stripped by the gateway).
        built = await super()._build_params(
            messages=messages,
            tools=tools,
            response_format=None,
            stream=stream,
            **kwargs,
        )
        params = built["params"]

        if schema_model is not None:
            serialized = AwsClaudeChatSerializer.serialize_response_format(schema_model, model_name=self.model)
            schema = serialized["json_schema"]["schema"]
            tool_name = schema_model.__name__
            structured_tool = {
                "name": tool_name,
                "description": f"Return the final answer strictly as a {tool_name} object.",
                "input_schema": schema,
            }
            params["tools"] = (params.get("tools") or []) + [structured_tool]
            # tool_choice {"type": "tool"} would silently disable thinking —
            # keep auto and instruct the model instead.
            params["tool_choice"] = {"type": "auto"}
            instruction = f"You must call the `{tool_name}` tool exactly once to return your final answer."
            params["system"] = f"{params['system']}\n\n{instruction}" if params.get("system") else instruction

        return built

    async def _format_response(
        self,
        response: Any,
        tools: Optional[List["Tool"]] = None,
        response_format: Optional[Union[Type[BaseModel], BaseModel, Dict]] = None,
    ) -> Response:
        schema_model = self._schema_model(response_format)
        if schema_model is None:
            return await super()._format_response(response=response, tools=tools, response_format=response_format)

        try:
            if hasattr(response, "content"):
                content = response.content or []
            elif isinstance(response, dict):
                content = response.get("content", [])
            else:
                content = []

            usage = self._get_usage(response)
            stop_reason = getattr(response, "stop_reason", None) if not isinstance(response, dict) else response.get("stop_reason")
            raw_response = response.model_dump() if hasattr(response, "model_dump") else response

            tool_name = schema_model.__name__
            raw: Optional[Any] = None
            fallback_raw: Optional[Any] = None
            text_parts: List[str] = []
            for item in content:
                item_type = getattr(item, "type", None) if not isinstance(item, dict) else item.get("type")
                if item_type == "tool_use":
                    name = getattr(item, "name", None) if not isinstance(item, dict) else item.get("name")
                    tool_input = getattr(item, "input", None) if not isinstance(item, dict) else item.get("input")
                    if name == tool_name:
                        raw = tool_input
                    elif fallback_raw is None:
                        fallback_raw = tool_input
                elif item_type == "text":
                    text = getattr(item, "text", None) if not isinstance(item, dict) else item.get("text")
                    if text:
                        text_parts.append(text)

            if raw is None:
                raw = fallback_raw
            if raw is None:
                # tool_choice is auto (to keep thinking on), so the model may
                # occasionally answer in text — try to parse that as JSON.
                raw = "\n".join(text_parts)

            if not raw:
                return Response(
                    type=ResponseType.LLM,
                    success=False,
                    message="Empty structured output from model",
                    data={"raw_response": raw_response, "stop_reason": stop_reason},
                )

            try:
                data = dirtyjson.loads(raw) if isinstance(raw, str) else raw
                parsed_model = schema_model.model_validate(data)
            except ValidationError as e:
                # dirtyjson tolerates truncated JSON, so an output cut off at
                # max_tokens surfaces as missing required fields — report the
                # truncation explicitly.
                if stop_reason == "max_tokens":
                    msg = f"Structured output truncated at max_tokens (stop_reason=max_tokens): {e}"
                else:
                    msg = f"Structured output failed schema validation: {e}"
                return Response(
                    type=ResponseType.LLM,
                    success=False,
                    message=msg,
                    data={"error": str(e), "content": raw, "stop_reason": stop_reason},
                )
            except (dirtyjson.Error, ValueError, TypeError) as e:
                return Response(
                    type=ResponseType.LLM,
                    success=False,
                    message=f"Failed to parse JSON from response: {e}",
                    data={"error": str(e), "content": raw, "stop_reason": stop_reason},
                )

            model_dict = parsed_model.model_dump()
            field_lines = [f"{field_name}={field_value!r}" for field_name, field_value in model_dict.items()]
            formatted_message = f"Response result:\n\n{schema_model.__name__}(\n"
            formatted_message += ",\n".join(f"    {line}" for line in field_lines)
            formatted_message += "\n)"

            return Response(
                type=ResponseType.LLM,
                success=True,
                message=formatted_message,
                data={
                    "raw_response": raw_response,
                    "usage": usage,
                    "stop_reason": stop_reason,
                },
                usage=TokenUsage.from_raw(usage),
                parsed_model=parsed_model,
            )
        except Exception as e:
            logger.error(f"Failed to format response: {e}")
            return Response(
                type=ResponseType.LLM,
                success=False,
                message=f"Failed to format response: {e}",
                data={"error": str(e)},
            )
