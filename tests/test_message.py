"""``.text`` is the one projection every consumer of a message reads.

Prompt assembly, token counting, memory records and the trajectory's SFT export all reach
for ``.text`` and none of them know whether the message underneath holds a bare string or
a list of content parts. That projection is built by joining ``str(part)``, so a part
whose ``__str__`` returns something unhelpful does not raise anywhere — it quietly
shortens the text, and the loss shows up as a model that was never shown the image, or a
token count that under-reports a turn.

The rest of the file pins the values providers read directly rather than through ``.text``:
the media types Anthropic requires on every attachment, and ``cache``, which decides where
Anthropic's cache breakpoints land.
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


# --------------------------------------------------------------------------- #
# The .text projection
# --------------------------------------------------------------------------- #
def test_a_string_message_reads_back_as_itself():
    """The simple shape must not be special-cased into a different one.

    ``content`` is a union: a string or a list of parts. Every caller reads ``.text``
    instead of branching on which it got, so the string case has to come back byte-identical
    — not wrapped, not stripped, not joined with anything.
    """
    for cls in (HumanMessage, SystemMessage, AssistantMessage):
        assert cls(content="plain").text == "plain"


def test_content_parts_are_joined_into_one_newline_separated_string():
    """The separator is load-bearing: parts are separate thoughts, not one run-on line.

    Joining on the empty string would glue the last word of one part to the first of the
    next, and the result still passes every "is a string" check downstream.
    """
    message = HumanMessage(
        content=[
            ContentPartText(text="first"),
            ContentPartText(text="second"),
        ]
    )
    assert message.text == "first\nsecond"


def test_a_media_part_contributes_its_url_to_the_text():
    """A message with four attachments must not read as text-only.

    Media parts have no text of their own, so the tempting implementation keeps the text
    parts and skips the rest — which loses nothing visible and everything real: the
    transcript then says the turn was a bare "describe", and a token count built from
    ``.text`` under-reports it. Each media part stringifies to its URL instead, so every
    part is represented in order.
    """
    message = HumanMessage(
        content=[
            ContentPartText(text="describe"),
            ContentPartImage(image_url=ImageURL(url="file:///tmp/a.png")),
            ContentPartAudio(audio_url=AudioURL(url="file:///tmp/a.mp3")),
            ContentPartVideo(video_url=VideoURL(url="file:///tmp/a.mp4")),
            ContentPartPdf(pdf_url=PdfURL(url="file:///tmp/a.pdf")),
        ]
    )
    assert message.text.splitlines() == [
        "describe",
        "file:///tmp/a.png",
        "file:///tmp/a.mp3",
        "file:///tmp/a.mp4",
        "file:///tmp/a.pdf",
    ]


def test_an_empty_part_list_is_empty_text_not_an_error():
    """An empty turn is a normal state; raising here would break assembly mid-prompt."""
    assert HumanMessage(content=[]).text == ""


def test_an_assistant_refusal_reads_as_its_text():
    """A refusal is content, not an absence of it.

    It arrives as its own part type with the text under ``refusal`` rather than ``text``,
    so a projection that only knows ``ContentPartText`` renders the turn as empty — and
    a history that shows an empty assistant turn where the model actually declined is
    misleading in the one situation someone is reading the history to understand.
    """
    message = AssistantMessage(content=[ContentPartRefusal(refusal="I cannot")])
    assert message.text == "I cannot"


# --------------------------------------------------------------------------- #
# What a class fixes about its messages
# --------------------------------------------------------------------------- #
def test_a_role_is_decided_by_the_message_class_not_by_the_caller():
    """The class is the only place the role is chosen, so it can never be mismatched.

    A role passed in at the call site would let a ``SystemMessage`` be constructed
    carrying ``role="user"``; providers accept that and the prompt silently stops being a
    system prompt.
    """
    assert HumanMessage(content="x").role == "user"
    assert SystemMessage(content="x").role == "system"
    assert AssistantMessage(content="x").role == "assistant"


def test_every_media_url_declares_a_media_type_without_being_asked():
    """Anthropic requires an explicit media type on every attachment.

    Nothing in the call site supplies one — an agent attaching a screenshot passes a URL
    and no more — so if the default were absent the request would fail at the provider,
    far from the code that built the message. Each URL type carries the type its format
    is overwhelmingly likely to be.
    """
    assert ImageURL(url="u").media_type == "image/png"
    assert AudioURL(url="u").media_type == "audio/mp3"
    assert VideoURL(url="u").media_type == "video/mp4"
    assert PdfURL(url="u").media_type == "application/pdf"


def test_image_detail_defaults_to_auto_and_accepts_the_documented_levels():
    """``detail`` is priced: "high" re-reads the image at full fidelity every turn.

    Defaulting to anything but ``auto`` would change what every image costs without any
    call site asking for it. The three levels are the ones the vision API defines; the
    field is a Literal so a fourth is rejected at construction rather than at the provider.
    """
    assert ImageURL(url="u").detail == "auto"
    for level in ("auto", "low", "high"):
        assert ImageURL(url="u", detail=level).detail == level


def test_caching_is_off_unless_asked_for():
    """``cache`` places an Anthropic cache breakpoint — it must never default on.

    Breakpoints are a limited resource and a cached prefix is billed differently from a
    fresh one, so a default of ``True`` would mark every message and quietly change both
    the cost and which prefix gets reused.
    """
    assert SystemMessage(content="x").cache is False
    assert SystemMessage(content="x", cache=True).cache is True


# --------------------------------------------------------------------------- #
# The forms that leave this process
# --------------------------------------------------------------------------- #
def test_tool_calls_default_to_empty_and_stringify_as_a_call():
    """The default is a list, so every consumer can iterate without a ``None`` check.

    The string form is what reaches a log or a prose transcript, and it has to keep the
    call id: without it, two calls to the same tool in one turn are indistinguishable
    from each other in the record.
    """
    assert AssistantMessage(content="x").tool_calls == []
    call = ToolCall(id="call_1", function=Function(name="bash", arguments='{"cmd":"ls"}'))
    assert str(call) == 'ToolCall[call_1]: bash({"cmd":"ls"})'


def test_messages_round_trip_through_the_model_dump_used_by_serializers():
    """Every provider serializer starts from ``model_dump``, so a lossy dump is silent.

    The union in ``content`` is what makes this worth asserting: pydantic has to pick the
    right part class back out of a bare dict on validate, and picking a wider one loses
    the fields the narrower one carried. ``detail`` is checked specifically because it is
    the field a re-validation drops without changing the shape of anything.
    """
    original = HumanMessage(
        content=[
            ContentPartText(text="hi"),
            ContentPartImage(image_url=ImageURL(url="u", detail="high")),
        ]
    )
    restored = HumanMessage.model_validate(original.model_dump())
    assert restored.text == original.text
    assert restored.content[1].image_url.detail == "high"
