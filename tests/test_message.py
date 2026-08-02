"""Message types: the ``.text`` projection and the string forms providers see.

``text`` is what every prompt-assembly and token-counting path reads, and it has
to behave the same whether a message carries a bare string or a list of content
parts. The ``__str__`` of each part is what feeds that projection, so the two are
tested together.
"""

from agentevolver.message import (
    AssistantMessage,
    AudioURL,
    ContentPartAudio,
    ContentPartImage,
    ContentPartPdf,
    ContentPartRefusal,
    ContentPartText,
    ContentPartVideo,
    Function,
    HumanMessage,
    ImageURL,
    PdfURL,
    SystemMessage,
    ToolCall,
    VideoURL,
)


def test_a_string_message_reads_back_as_itself():
    for cls in (HumanMessage, SystemMessage, AssistantMessage):
        assert cls(content="plain").text == "plain"


def test_content_parts_join_on_newlines():
    message = HumanMessage(content=[
        ContentPartText(text="first"),
        ContentPartText(text="second"),
    ])
    assert message.text == "first\nsecond"


def test_a_media_part_contributes_its_url_to_the_text():
    """Media parts stringify to their URL — that is what lands in ``text``."""
    message = HumanMessage(content=[
        ContentPartText(text="describe"),
        ContentPartImage(image_url=ImageURL(url="file:///tmp/a.png")),
        ContentPartAudio(audio_url=AudioURL(url="file:///tmp/a.mp3")),
        ContentPartVideo(video_url=VideoURL(url="file:///tmp/a.mp4")),
        ContentPartPdf(pdf_url=PdfURL(url="file:///tmp/a.pdf")),
    ])
    assert message.text.splitlines() == [
        "describe",
        "file:///tmp/a.png",
        "file:///tmp/a.mp3",
        "file:///tmp/a.mp4",
        "file:///tmp/a.pdf",
    ]


def test_an_empty_part_list_is_empty_text_not_an_error():
    assert HumanMessage(content=[]).text == ""


def test_roles_are_fixed_per_class():
    assert HumanMessage(content="x").role == "user"
    assert SystemMessage(content="x").role == "system"
    assert AssistantMessage(content="x").role == "assistant"


def test_media_parts_default_to_a_declared_media_type():
    """Anthropic needs an explicit media type; every URL type carries a default."""
    assert ImageURL(url="u").media_type == "image/png"
    assert AudioURL(url="u").media_type == "audio/mp3"
    assert VideoURL(url="u").media_type == "video/mp4"
    assert PdfURL(url="u").media_type == "application/pdf"


def test_image_detail_defaults_to_auto_and_accepts_the_documented_levels():
    assert ImageURL(url="u").detail == "auto"
    for level in ("auto", "low", "high"):
        assert ImageURL(url="u", detail=level).detail == level


def test_an_assistant_refusal_reads_as_its_text():
    message = AssistantMessage(content=[ContentPartRefusal(refusal="I cannot")])
    assert message.text == "I cannot"


def test_tool_calls_default_to_empty_and_stringify_as_a_call():
    assert AssistantMessage(content="x").tool_calls == []
    call = ToolCall(id="call_1", function=Function(name="bash", arguments='{"cmd":"ls"}'))
    assert str(call) == 'ToolCall[call_1]: bash({"cmd":"ls"})'


def test_messages_round_trip_through_the_model_dump_used_by_serializers():
    original = HumanMessage(content=[
        ContentPartText(text="hi"),
        ContentPartImage(image_url=ImageURL(url="u", detail="high")),
    ])
    restored = HumanMessage.model_validate(original.model_dump())
    assert restored.text == original.text
    assert restored.content[1].image_url.detail == "high"


def test_caching_is_off_unless_asked_for():
    """``cache`` drives Anthropic cache breakpoints — it must never default on."""
    assert SystemMessage(content="x").cache is False
    assert SystemMessage(content="x", cache=True).cache is True
