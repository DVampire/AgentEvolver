from pydantic import BaseModel, Field
from typing import Any, Dict, Literal, List, Union, Optional

class ContentPartText(BaseModel):
    """A text content part."""
    text: str = Field(description="The text of the content part.")  # type: ignore
    type: Literal['text'] = Field(default='text', description="The type of the content part.")  # type: ignore
    
    def __str__(self) -> str:
        return str(self.text)

    def __repr__(self) -> str:
        return f'ContentPartText(text={repr(self.text)})'

SupportedImageMediaType = Literal['image/jpeg', 'image/png', 'image/gif', 'image/webp']
SupportedAudioMediaType = Literal['audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/mp3', 'audio/m4a', 'audio/flac']
SupportedVideoMediaType = Literal['video/mp4', 'video/mpeg', 'video/ogg', 'video/webm', 'video/mov']
SupportedPdfMediaType = Literal['application/pdf']

class AudioURL(BaseModel):
    """An audio URL content part."""
    url: str = Field(description="The URL of the audio.")
    type: Literal['audio_url'] = Field(default='audio_url', description="The type of the content part.")  # type: ignore
    
    media_type: SupportedAudioMediaType = Field(default='audio/mp3', description="The media type of the audio.")

    def __str__(self) -> str:
        return str(self.url)

    def __repr__(self) -> str:
        return f'AudioURL(url={repr(self.url)}, media_type={repr(self.media_type)})'


class ContentPartAudio(BaseModel):
    """An audio content part."""
    audio_url: AudioURL = Field(description="The URL of the audio.")
    type: Literal['audio_url'] = Field(default='audio_url', description="The type of the content part.")  # type: ignore

    def __str__(self) -> str:
        return str(self.audio_url)

    def __repr__(self) -> str:
        return f'ContentPartAudio(audio_url={str(self.audio_url)})'

class VideoURL(BaseModel):
    """A video URL content part."""
    url: str = Field(description="The URL of the video.")
    type: Literal['video_url'] = Field(default='video_url', description="The type of the content part.")  # type: ignore
    
    media_type: SupportedVideoMediaType = Field(default='video/mp4', description="The media type of the video.")

    def __str__(self) -> str:
        return str(self.url)

    def __repr__(self) -> str:
        return f'VideoURL(url={repr(self.url)}, media_type={repr(self.media_type)})'

class ContentPartVideo(BaseModel):
    """A video content part."""
    video_url: VideoURL = Field(description="The URL of the video.")
    type: Literal['video_url'] = Field(default='video_url', description="The type of the content part.")  # type: ignore

    def __str__(self) -> str:
        return str(self.video_url)

    def __repr__(self) -> str:
        return f'ContentPartVideo(video_url={repr(self.video_url)})'

class ImageURL(BaseModel):
    """An image URL content part."""
    url: str = Field(description="Either a URL of the image or the base64 encoded image data.")
    detail: Literal['auto', 'low', 'high'] = Field(default='auto', description="Specifies the detail level of the image.")  # type: ignore  
    """Specifies the detail level of the image.
    Learn more in the
    [Vision guide](https://platform.openai.com/docs/guides/vision#low-or-high-fidelity-image-understanding).
    """
    
    # needed for Anthropic
    media_type: SupportedImageMediaType = 'image/png'

    def __str__(self) -> str:
        return str(self.url)

    def __repr__(self) -> str:
        return f'ImageURL(url={repr(self.url)}, detail={repr(self.detail)}, media_type={repr(self.media_type)})'

class ContentPartImage(BaseModel):
    """An image content part."""
    image_url: ImageURL = Field(description="The URL of the image.")
    type: Literal['image_url'] = Field(default='image_url', description="The type of the content part.")  # type: ignore

    def __str__(self) -> str:
        return str(self.image_url)

    def __repr__(self) -> str:
        return f'ContentPartImage(image_url={repr(self.image_url)})'

class PdfURL(BaseModel):
    """A PDF URL content part."""
    url: str = Field(description="The URL of the PDF.")
    type: Literal['pdf_url'] = Field(default='pdf_url', description="The type of the content part.")  # type: ignore
    
    media_type: SupportedPdfMediaType = 'application/pdf'

    def __str__(self) -> str:
        return str(self.url)

    def __repr__(self) -> str:
        return f'PdfURL(url={repr(self.url)}, media_type={repr(self.media_type)})'

class ContentPartPdf(BaseModel):
    """A PDF content part."""
    pdf_url: PdfURL = Field(description="The URL of the PDF.")
    type: Literal['pdf_url'] = Field(default='pdf_url', description="The type of the content part.")  # type: ignore

    def __str__(self) -> str:
        return str(self.pdf_url)

    def __repr__(self) -> str:
        return f'ContentPartPdf(pdf_url={repr(self.pdf_url)})'

class ContentPartRefusal(BaseModel):
    refusal: str = Field(description="The refusal message by the assistant.")
    type: Literal['refusal'] = Field(default='refusal', description="The type of the content part.")  # type: ignore

    def __str__(self) -> str:
        return str(self.refusal)

    def __repr__(self) -> str:
        return f'ContentPartRefusal(refusal={repr(self.refusal)})'
    
class Function(BaseModel):
    arguments: str = Field(description="The arguments to call the function with, as generated by the model in JSON format. Note that the model does not always generate valid JSON, and may hallucinate parameters not defined by your function schema. Validate the arguments in your code before calling your function.")
    name: str = Field(description="The name of the function to call.")

    def __str__(self) -> str:
        return f'{self.name}({(self.arguments)})'

    def __repr__(self) -> str:
        args_repr = repr(self.arguments)
        return f'Function(name={repr(self.name)}, arguments={args_repr})'


class ToolCall(BaseModel):
    id: str = Field(description="The ID of the tool call.")
    function: Function = Field(description="The function that the model called.")
    type: Literal['function'] = Field(default='function', description="The type of the tool. Currently, only `function` is supported.")  # type: ignore
    # Opaque linkage used by hosted program runtimes. Ordinary direct calls leave this
    # empty; Responses PTC requires it to be echoed on function_call_output unchanged.
    caller: Optional[Dict[str, Any]] = Field(default=None)

    def __str__(self) -> str:
        return f'ToolCall[{self.id}]: {str(self.function)}'

    def __repr__(self) -> str:
        return f'ToolCall(id={repr(self.id)}, function={repr(self.function)})'


# region - Message types
class Message(BaseModel):
    """Base class for all message types"""
    role: Literal['user', 'system', 'assistant', 'tool'] = Field(description="The role of the message.")  # type: ignore
    cache: bool = Field(default=False, description="Whether to cache this message. This is only applicable when using Anthropic models.")  # type: ignore
    # Framework-only protocol metadata. Provider serializers select their wire fields
    # explicitly, and this field is excluded from ordinary model dumps so it can never
    # leak into an API request or distort token estimates. RequestSnapshot records it
    # separately for exact four-layer diagnostics.
    context_layer: Optional[Literal['fixed', 'checkpoint', 'recent', 'live']] = Field(
        default=None, exclude=True,
    )
 
class HumanMessage(Message):
    """A message from a human user."""
    role: Literal['user'] = Field(default='user', description="The role of the messages author, in this case `user`.")  # type: ignore
    content: Union[str, List[Union[
        ContentPartText, 
        ContentPartImage,
        ContentPartAudio,
        ContentPartVideo,
        ContentPartPdf,
        ]]] = Field(description="The contents of the user message.")
    name: Optional[str] = Field(default=None, description="An optional name for the participant. Provides the model information to differentiate between participants of the same role.")  # type: ignore

    @property
    def text(self) -> str:
        """
        Automatically parse the text inside content, whether it's a string or a list of content parts.
        """
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, list):
            return '\n'.join([str(part) for part in self.content])
        else:
            return ''

    def __str__(self) -> str:
        return f'HumanMessage(content={self.text})'

    def __repr__(self) -> str:
        return f'HumanMessage(content={repr(self.text)})'


class CompactionMessage(HumanMessage):
    """One conversation checkpoint with an optional provider-native payload.

    The readable ``content`` is the portable checkpoint used by chat providers and by
    request inspection.  ``provider_state`` may additionally carry an opaque Responses
    ``compaction`` item.  Keeping both representations on one canonical message lets a
    run stay portable without flattening provider-owned state into prose.
    """

    provider_state: Dict[str, Any] = Field(default_factory=dict)
    
    
class SystemMessage(Message):
    role: Literal['system'] = Field(default='system', description="The role of the messages author, in this case `system`.")  # type: ignore
    content: Union[str, List[Union[
        ContentPartText, 
        ContentPartImage,
        ContentPartAudio,   
        ContentPartVideo,
        ContentPartPdf,
    ]]] = Field(description="The contents of the system message.")
    name: Optional[str] = Field(default=None, description="An optional name for the participant. Provides the model information to differentiate between participants of the same role.")  # type: ignore

    @property
    def text(self) -> str:
        """
        Automatically parse the text inside content, whether it's a string or a list of content parts.
        """
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, list):
            return '\n'.join([str(part) for part in self.content])
        else:
            return ''

    def __str__(self) -> str:
        return f'SystemMessage(content={self.text})'

    def __repr__(self) -> str:
        return f'SystemMessage(content={repr(self.text)})'


class AssistantMessage(Message):
    role: Literal['assistant'] = Field(default='assistant', description="The role of the messages author, in this case `assistant`.")  # type: ignore
    content: Union[str, List[Union[
        ContentPartText, 
        ContentPartImage,
        ContentPartAudio,
        ContentPartVideo,
        ContentPartPdf,
        ContentPartRefusal,
    ]]] = Field(description="The contents of the assistant message.")
    name: Optional[str] = Field(default=None, description="An optional name for the participant. Provides the model information to differentiate between participants of the same role.")  # type: ignore
    refusal: Optional[str] = Field(default=None, description="The refusal message by the assistant.")  # type: ignore
    tool_calls: List[ToolCall] = Field(default=[], description="The tool calls generated by the model, such as function calls.")  # type: ignore
    # Opaque provider-owned items required when an assistant turn is replayed.  For
    # example, Anthropic requires thinking blocks (including signatures) unchanged,
    # while the Responses API expects prior reasoning output items in manual history.
    provider_state: Dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        """
        Automatically parse the text inside content, whether it's a string or a list of content parts.
        """
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, list):
            return '\n'.join([str(part) for part in self.content])
        else:
            return ''

    def __str__(self) -> str:
        return f'AssistantMessage(content={self.text})'

    def __repr__(self) -> str:
        return f'AssistantMessage(content={repr(self.text)})'


class ToolMessage(Message):
    """A tool's result, answering one assistant tool call.

    The turn a history could not previously express. `AssistantMessage` already
    carried `tool_calls`, but nothing carried what came back, so a transcript had to
    describe results in prose instead of replaying them in the shape the model was
    trained on.

    `tool_call_id` matches the `ToolCall.id` the model issued. Pairing by position
    would work only while both ends survive in order; an id survives replay, a log
    read out of context, and a range folded away by compaction.
    """

    role: Literal['tool'] = Field(default='tool', description="The role of the messages author, in this case `tool`.")  # type: ignore
    content: str = Field(description="The tool's result, as the model should see it.")
    tool_call_id: str = Field(description="Id of the assistant tool call this result answers.")
    name: Optional[str] = Field(default=None, description="The tool's name. Carried because not every provider pairs a result to its call by id: Gemini pairs by declared function name, so a message without it cannot be replayed there at all.")
    is_error: bool = Field(default=False, description="Whether the call failed. Kept out of `content` so a consumer can style or count failures without parsing prose.")
    caller: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Opaque provider caller linkage copied from the matching tool call.",
    )

    @property
    def text(self) -> str:
        return self.content

    def __str__(self) -> str:
        return f'ToolMessage(tool_call_id={self.tool_call_id}, content={self.text})'

    def __repr__(self) -> str:
        return f'ToolMessage(tool_call_id={self.tool_call_id}, content={repr(self.text)})'
