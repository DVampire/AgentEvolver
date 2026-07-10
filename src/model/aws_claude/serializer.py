from typing import overload, Any, Union, List, Dict, Type
import base64
import io
import os

try:
    from openai.types.chat import (
        ChatCompletionAssistantMessageParam,
        ChatCompletionContentPartImageParam,
        ChatCompletionContentPartRefusalParam,
        ChatCompletionContentPartTextParam,
        ChatCompletionMessageFunctionToolCallParam,
        ChatCompletionMessageParam,
        ChatCompletionSystemMessageParam,
        ChatCompletionUserMessageParam,
    )
    from openai.types.chat.chat_completion_content_part_image_param import ImageURL as OpenAIImageURL
    from openai.types.chat.chat_completion_message_function_tool_call_param import Function as OpenAIFunction
except ImportError:
    # Fallback types if openai package is not available
    ChatCompletionAssistantMessageParam = dict
    ChatCompletionContentPartImageParam = dict
    ChatCompletionContentPartRefusalParam = dict
    ChatCompletionContentPartTextParam = dict
    ChatCompletionMessageFunctionToolCallParam = dict
    ChatCompletionMessageParam = dict
    ChatCompletionSystemMessageParam = dict
    ChatCompletionUserMessageParam = dict
    OpenAIImageURL = dict
    OpenAIFunction = dict

from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel

from src.message.types import (
    AssistantMessage,
    ContentPartAudio,
    ContentPartImage,
    ContentPartPdf,
    ContentPartRefusal,
    ContentPartText,
    ContentPartVideo,
    HumanMessage,
    Message,
    SystemMessage,
    ToolCall,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tool.types import Tool

from src.utils import assemble_project_path, encode_file_base64


def _strict_incompatible(o: Any) -> bool:
    """True if a JSON schema cannot be used with OpenAI strict structured outputs.

    Strict mode requires, for EVERY object: (1) additionalProperties: false, and
    (2) `required` to list every key in `properties`. A Dict[str, Any] field
    violates (1); any optional/defaulted field violates (2). Either one makes
    strict=true 400 with invalid_json_schema (observed on openai/gpt-5.5). When this
    returns True the caller sends strict=false — OpenAI then relaxes both rules and
    the result is still validated downstream via pydantic.
    """
    if isinstance(o, dict):
        if o.get("additionalProperties") is True:
            return True
        if o.get("type") == "object" or "properties" in o:
            props = set((o.get("properties") or {}).keys())
            req = set(o.get("required") or [])
            if props - req:
                return True
        return any(_strict_incompatible(v) for v in o.values())
    if isinstance(o, list):
        return any(_strict_incompatible(v) for v in o)
    return False


class AwsClaudeChatSerializer:
    """
    Serializer for converting between custom message types and the AWS Claude
    (OpenAI-compatible) chat completions API message param types.

    The AWS Claude gateway exposes an OpenAI-compatible `/chat/completions`
    endpoint, so this serializer follows the same shape as the OpenAI/OpenRouter
    serializers.
    """

    @staticmethod
    def _serialize_content_part_text(part: ContentPartText) -> ChatCompletionContentPartTextParam:
        return ChatCompletionContentPartTextParam(text=part.text, type='text')

    @staticmethod
    def _resize_image_if_needed(image_bytes: bytes, max_dim: int = 8000) -> bytes:
        """Resize image so neither dimension exceeds max_dim. Returns original bytes if no resize needed."""
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))
            w, h = img.size
            if w <= max_dim and h <= max_dim:
                return image_bytes
            scale = min(max_dim / w, max_dim / h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            buf = io.BytesIO()
            fmt = img.format or "JPEG"
            if fmt.upper() == "JPG":
                fmt = "JPEG"
            img.save(buf, format=fmt)
            return buf.getvalue()
        except Exception:
            return image_bytes

    @staticmethod
    def _serialize_content_part_image(part: ContentPartImage) -> ChatCompletionContentPartImageParam:
        url = part.image_url.url

        # For local files and data URLs, resize if needed before sending to AWS Bedrock
        image_bytes: bytes | None = None
        media_type = "image/jpeg"

        if url.startswith("http://") or url.startswith("https://"):
            try:
                import httpx
                response = httpx.get(url, timeout=30, follow_redirects=True)
                response.raise_for_status()
                image_bytes = response.content
                content_type = response.headers.get("content-type", "image/jpeg")
                media_type = content_type.split(";")[0].strip() or "image/jpeg"
            except Exception:
                image_bytes = None
        elif url.startswith("data:"):
            # data:<media_type>;base64,<data>
            if "," in url:
                header, data = url.split(",", 1)
                if "image/" in header:
                    media_type = header.split(";")[0].split("data:")[1]
                image_bytes = base64.b64decode(data)
        elif url.startswith("file://"):
            file_path = url[7:]
            if not os.path.isabs(file_path):
                file_path = assemble_project_path(file_path)
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    image_bytes = f.read()
                ext = os.path.splitext(file_path)[1].lower()
                media_type = {"jpg": "image/jpeg", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                              ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")
        elif os.path.exists(url):
            with open(url, "rb") as f:
                image_bytes = f.read()
            ext = os.path.splitext(url)[1].lower()
            media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                          ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")
        elif os.path.exists(assemble_project_path(url)):
            file_path = assemble_project_path(url)
            with open(file_path, "rb") as f:
                image_bytes = f.read()
            ext = os.path.splitext(file_path)[1].lower()
            media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                          ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")

        if image_bytes is not None:
            resized = AwsClaudeChatSerializer._resize_image_if_needed(image_bytes)
            b64 = base64.b64encode(resized).decode("utf-8")
            url = f"data:{media_type};base64,{b64}"

        return ChatCompletionContentPartImageParam(
            image_url=OpenAIImageURL(url=url, detail=part.image_url.detail),
            type='image_url',
        )

    @staticmethod
    def _serialize_content_part_refusal(part: ContentPartRefusal) -> ChatCompletionContentPartRefusalParam:
        return ChatCompletionContentPartRefusalParam(refusal=part.refusal, type='refusal')

    @staticmethod
    def _serialize_content_part_audio(part: ContentPartAudio) -> dict[str, Any]:
        """Serialize audio content part for the OpenAI-compatible API.

        Expects: {"type": "input_audio", "input_audio": {"data": "...", "format": "wav"}}
        """
        audio_url = part.audio_url.url
        audio_format = part.audio_url.media_type.split('/')[-1]

        if audio_url.startswith("data:"):
            if "," in audio_url:
                header, data = audio_url.split(",", 1)
                if "audio/" in header:
                    format_part = header.split("audio/")[1].split(";")[0]
                    if format_part:
                        audio_format = format_part
                return {
                    "type": "input_audio",
                    "input_audio": {"data": data, "format": audio_format},
                }
        elif audio_url.startswith("file://"):
            file_path = audio_url[7:]
            if not os.path.isabs(file_path):
                file_path = assemble_project_path(file_path)
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    audio_data = f.read()
                base64_data = base64.b64encode(audio_data).decode("utf-8")
                return {
                    "type": "input_audio",
                    "input_audio": {"data": base64_data, "format": audio_format},
                }
        elif os.path.exists(audio_url):
            with open(audio_url, "rb") as f:
                audio_data = f.read()
            base64_data = base64.b64encode(audio_data).decode("utf-8")
            return {
                "type": "input_audio",
                "input_audio": {"data": base64_data, "format": audio_format},
            }
        elif os.path.exists(assemble_project_path(audio_url)):
            file_path = assemble_project_path(audio_url)
            with open(file_path, "rb") as f:
                audio_data = f.read()
            base64_data = base64.b64encode(audio_data).decode("utf-8")
            return {
                "type": "input_audio",
                "input_audio": {"data": base64_data, "format": audio_format},
            }
        else:
            return {
                "type": "input_audio",
                "input_audio": {"url": audio_url, "format": audio_format},
            }

    @staticmethod
    def _serialize_content_part_video(part: ContentPartVideo) -> dict[str, Any]:
        """Serialize video content part. Expects: {"type": "video_url", "video_url": {"url": "..."}}"""
        return {
            "type": "video_url",
            "video_url": {"url": part.video_url.url},
        }

    @staticmethod
    def _serialize_content_part_pdf(part: ContentPartPdf) -> dict[str, Any]:
        """Serialize PDF content part. Expects: {"type": "file", "file": {"filename": ..., "file_data": data_url}}"""
        pdf_url = part.pdf_url.url

        if pdf_url.startswith("data:"):
            return {
                "type": "file",
                "file": {"filename": "document.pdf", "file_data": pdf_url},
            }
        elif pdf_url.startswith("file://"):
            file_path = pdf_url[7:]
            if not os.path.isabs(file_path):
                file_path = assemble_project_path(file_path)
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    pdf_data = f.read()
                base64_data = base64.b64encode(pdf_data).decode("utf-8")
                filename = os.path.basename(file_path)
                data_url = f"data:application/pdf;base64,{base64_data}"
                return {
                    "type": "file",
                    "file": {"filename": filename, "file_data": data_url},
                }
        elif os.path.exists(pdf_url):
            with open(pdf_url, "rb") as f:
                pdf_data = f.read()
            base64_data = base64.b64encode(pdf_data).decode("utf-8")
            filename = os.path.basename(pdf_url)
            data_url = f"data:application/pdf;base64,{base64_data}"
            return {
                "type": "file",
                "file": {"filename": filename, "file_data": data_url},
            }
        elif os.path.exists(assemble_project_path(pdf_url)):
            file_path = assemble_project_path(pdf_url)
            with open(file_path, "rb") as f:
                pdf_data = f.read()
            base64_data = base64.b64encode(pdf_data).decode("utf-8")
            filename = os.path.basename(file_path)
            data_url = f"data:application/pdf;base64,{base64_data}"
            return {
                "type": "file",
                "file": {"filename": filename, "file_data": data_url},
            }
        else:
            return {
                "type": "file",
                "file": {
                    "filename": "document.pdf",
                    "file_data": pdf_url if pdf_url.startswith("data:") else f"data:application/pdf;base64,{pdf_url}",
                },
            }

    @staticmethod
    def _serialize_user_content(
        content: str | list[ContentPartText | ContentPartImage | ContentPartAudio | ContentPartVideo | ContentPartPdf],
    ) -> str | list[Union[ChatCompletionContentPartTextParam, ChatCompletionContentPartImageParam, dict[str, Any]]]:
        """Serialize content for user messages (text, images, audio, video, and PDF allowed)."""
        if isinstance(content, str):
            return content
        serialized_parts: list[Union[ChatCompletionContentPartTextParam, ChatCompletionContentPartImageParam, dict[str, Any]]] = []
        for part in content:
            if part.type == 'text':
                serialized_parts.append(AwsClaudeChatSerializer._serialize_content_part_text(part))
            elif part.type == 'image_url':
                serialized_parts.append(AwsClaudeChatSerializer._serialize_content_part_image(part))
            elif part.type == 'audio_url':
                serialized_parts.append(AwsClaudeChatSerializer._serialize_content_part_audio(part))
            elif part.type == 'video_url':
                serialized_parts.append(AwsClaudeChatSerializer._serialize_content_part_video(part))
            elif part.type == 'pdf_url':
                serialized_parts.append(AwsClaudeChatSerializer._serialize_content_part_pdf(part))
        return serialized_parts

    @staticmethod
    def _serialize_system_content(
        content: str | list[ContentPartText],
    ) -> str | list[ChatCompletionContentPartTextParam]:
        """Serialize content for system messages (text only)."""
        if isinstance(content, str):
            return content
        serialized_parts: list[ChatCompletionContentPartTextParam] = []
        for part in content:
            if part.type == 'text':
                serialized_parts.append(AwsClaudeChatSerializer._serialize_content_part_text(part))
        return serialized_parts

    @staticmethod
    def _serialize_assistant_content(
        content: str | list[ContentPartText | ContentPartRefusal] | None,
    ) -> str | list[ChatCompletionContentPartTextParam | ChatCompletionContentPartRefusalParam] | None:
        """Serialize content for assistant messages (text and refusal allowed)."""
        if content is None:
            return None
        if isinstance(content, str):
            return content
        serialized_parts: list[ChatCompletionContentPartTextParam | ChatCompletionContentPartRefusalParam] = []
        for part in content:
            if part.type == 'text':
                serialized_parts.append(AwsClaudeChatSerializer._serialize_content_part_text(part))
            elif part.type == 'refusal':
                serialized_parts.append(AwsClaudeChatSerializer._serialize_content_part_refusal(part))
        return serialized_parts

    @staticmethod
    def _serialize_tool_call(tool_call: ToolCall) -> ChatCompletionMessageFunctionToolCallParam:
        return ChatCompletionMessageFunctionToolCallParam(
            id=tool_call.id,
            function=OpenAIFunction(name=tool_call.function.name, arguments=tool_call.function.arguments),
            type='function',
        )

    # region - Serialize overloads

    @overload
    @staticmethod
    def serialize_message(message: HumanMessage) -> ChatCompletionUserMessageParam: ...

    @overload
    @staticmethod
    def serialize_message(message: SystemMessage) -> ChatCompletionSystemMessageParam: ...

    @overload
    @staticmethod
    def serialize_message(message: AssistantMessage) -> ChatCompletionAssistantMessageParam: ...

    @staticmethod
    def serialize_message(message: Message) -> ChatCompletionMessageParam:
        """Serialize a custom message to an OpenAI-compatible message param."""
        if isinstance(message, HumanMessage):
            user_result: ChatCompletionUserMessageParam = {
                'role': 'user',
                'content': AwsClaudeChatSerializer._serialize_user_content(message.content),
            }
            if message.name is not None:
                user_result['name'] = message.name
            return user_result

        elif isinstance(message, SystemMessage):
            system_result: ChatCompletionSystemMessageParam = {
                'role': 'system',
                'content': [
                    {
                        'type': 'text',
                        'text': AwsClaudeChatSerializer._serialize_system_content(message.content),
                        "cache_control": {
                            "type": "ephemeral"
                        }
                    }
                ],
            }
            if message.name is not None:
                system_result['name'] = message.name
            return system_result

        elif isinstance(message, AssistantMessage):
            content = None
            if message.content is not None:
                content = AwsClaudeChatSerializer._serialize_assistant_content(message.content)
            assistant_result: ChatCompletionAssistantMessageParam = {'role': 'assistant'}
            if content is not None:
                assistant_result['content'] = content
            if message.name is not None:
                assistant_result['name'] = message.name
            if message.refusal is not None:
                assistant_result['refusal'] = message.refusal
            if message.tool_calls:
                assistant_result['tool_calls'] = [AwsClaudeChatSerializer._serialize_tool_call(tc) for tc in message.tool_calls]
            return assistant_result

        else:
            raise ValueError(f'Unknown message type: {type(message)}')

    @staticmethod
    def serialize_messages(messages: list[Message]) -> list[ChatCompletionMessageParam]:
        return [AwsClaudeChatSerializer.serialize_message(m) for m in messages]

    @staticmethod
    def serialize_tool(tool: "Tool") -> Dict[str, Any]:
        """Serialize a Tool instance to an OpenAI-compatible tool param."""
        return tool.function_calling

    @staticmethod
    def serialize_tools(tools: List["Tool"]) -> List[Dict[str, Any]]:
        """Serialize a list of Tool instances to an OpenAI-compatible tools param."""
        return [AwsClaudeChatSerializer.serialize_tool(tool) for tool in tools]

    @staticmethod
    def serialize_response_format(
        response_format: Union[Type[BaseModel], BaseModel],
        model_name: str = "claude-sonnet-4-20250514",
    ) -> Dict[str, Any]:
        """
        Format response_format from a Pydantic model to OpenAI JSON schema format.

        Output shape:
        {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "strict": True,
                "schema": { ... },
            },
        }
        """
        model_class = response_format if isinstance(response_format, type) else type(response_format)
        schema = model_class.model_json_schema()
        defs = schema.pop("$defs", {})  # Remove $defs to avoid appearing in the final result

        def transform(obj: Any) -> Any:
            if not isinstance(obj, dict):
                return obj

            # Expand all references to ensure full inlining
            if "$ref" in obj:
                ref_path = obj["$ref"]
                if ref_path.startswith("#/$defs/"):
                    def_name = ref_path.split("/")[-1]
                    if def_name in defs:
                        return transform(defs[def_name])
                return {"type": "object", "additionalProperties": False}

            # Handle Union structures (anyOf, oneOf, allOf) - used for Optional fields
            for k in ["anyOf", "oneOf", "allOf"]:
                if k in obj:
                    items = obj[k]
                    non_null = [i for i in items if isinstance(i, dict) and i.get("type") != "null"]
                    if len(non_null) == 1:
                        result = transform(non_null[0])
                        if isinstance(result, dict):
                            if "description" in obj and "description" not in result:
                                result["description"] = obj["description"]
                            if "title" in obj and "title" not in result:
                                result["title"] = obj["title"]
                        return result
                    else:
                        return {
                            "type": "object",
                            "description": obj.get("description", "Simplified Object"),
                            "additionalProperties": False
                        }

            # Handle objects
            if obj.get("type") == "object" or "properties" in obj:
                props = obj.get("properties", {})
                required = obj.get("required", [])
                new_props = {}
                new_required = []

                for k, v in props.items():
                    new_props[k] = transform(v)
                    if k in required:
                        new_required.append(k)

                ap = obj.get("additionalProperties", None)
                if isinstance(ap, dict):
                    additional_props = transform(ap)
                else:
                    if not new_props and ap is True:
                        additional_props = True
                    else:
                        additional_props = False

                result = {
                    "type": "object",
                    "properties": new_props,
                    "required": new_required,
                    "additionalProperties": additional_props
                }
                if "description" in obj:
                    result["description"] = obj["description"]
                if "title" in obj:
                    result["title"] = obj["title"]
                return result

            # Handle enum fields — add explicit "type" if missing so models
            # understand the expected value type (e.g. Literal["a", "b"] → string enum).
            if "enum" in obj and "type" not in obj:
                values = obj["enum"]
                if values and all(isinstance(v, str) for v in values):
                    result = {"type": "string", **obj}
                    return result
                elif values and all(isinstance(v, (int, float)) for v in values):
                    result = {"type": "number", **obj}
                    return result

            # Handle arrays
            if obj.get("type") == "array":
                result = {
                    "type": "array",
                    "items": transform(obj.get("items", {}))
                }
                if "description" in obj:
                    result["description"] = obj["description"]
                if "title" in obj:
                    result["title"] = obj["title"]
                return result

            return obj

        transformed = transform(schema)

        strict = not _strict_incompatible(transformed)

        return {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "strict": strict,
                "schema": transformed,
            },
        }
