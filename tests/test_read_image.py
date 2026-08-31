"""An image reaches the model or the call is refused; there is no third outcome.

The failure this prevents is the quiet one. A tool result here is a string — the agent loop
takes `Response.message` and drops everything else — so a `read_image_tool` that returned a
cheerful "attached the screenshot" and nothing else would look correct in every log, in the
trace, and to the model, which would then describe an image it was never shown. Two things
have to hold together for that not to happen: the bytes have to be committed somewhere the
next request can find them, and the turn that goes out has to actually carry them.

The refusal is the same shape of problem from the other side. Sending an image on a route
with no image input fails inside the provider, in an error naming neither this tool nor the
file, after the call is paid for — and the image is in the turn by then, so every retry on
that route fails identically. The gate therefore has to read the model's declared
capability, run before any file is touched, and say plainly that the file is not the
problem.
"""

import asyncio
import struct
import zlib
from types import SimpleNamespace

import pytest

from agentevolver.attachment import AttachmentError, attachment_manager
from agentevolver.message.types import ContentPartImage
from agentevolver.model.types import ModelConfig
from agentevolver.tool.default.read_image import ReadImageTool


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _png_bytes(width=2, height=2):
    """A real, minimal PNG. A stub of the right first eight bytes would pass the
    signature check while proving nothing about what the store does with the rest."""
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag, payload):
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def vision_model():
    """Register one model under the name the context routes to, then take it back out.

    Registered in the real catalog rather than stubbed over `get_model_config`: the tool's
    job is to read a declared capability off a registered model, and a stub that always
    answers would not notice if it stopped looking there.
    """
    from agentevolver.model import model_manager

    registered = []

    def install(supports_vision):
        config = ModelConfig(
            model_name="test/seer",
            model_type="chat/completions",
            model_id="seer",
            provider="test",
            supports_vision=supports_vision,
        )
        model_manager.model_context_manager.models[config.model_name] = config
        registered.append(config.model_name)
        return config

    yield install
    for name in registered:
        model_manager.model_context_manager.models.pop(name, None)


@pytest.fixture
def ctx():
    """A context carrying an id and a routed model, as an agent turn leaves it."""
    session = SimpleNamespace(id="session-under-test", extra={"model_name": "test/seer"})
    yield session
    attachment_manager.release(session.id)


def _read(path, ctx):
    return asyncio.run(ReadImageTool()(path=str(path), ctx=ctx))


# --------------------------------------------------------------------------- #
# The route gate
# --------------------------------------------------------------------------- #
def test_a_model_without_image_input_refuses_before_the_file_is_touched(
    tmp_path, vision_model, ctx
):
    """The gate must not be a post-hoc check on a file that was already read and stored.

    Order is the whole point: a refusal that happens after the read leaves a committed
    attachment behind, and the next turn sends it on the route that cannot carry it — the
    exact failure the gate exists to prevent, now with no tool call to blame.
    """
    vision_model(supports_vision=False)
    missing = tmp_path / "never-created.png"

    result = _read(missing, ctx)

    assert result.success is False
    # Not "file not found": the file was never consulted, and the model must not be sent
    # off to fix a path when the path is fine.
    assert "does not accept image input" in result.message
    assert attachment_manager.live(ctx.id) == []


def test_the_refusal_says_the_file_is_not_the_problem(tmp_path, vision_model, ctx):
    """A bare "cannot read image" invites the one recovery that cannot work.

    The model's default move on a failed read is to try another path, another format,
    another tool. Every one of those costs a call and fails the same way, because the
    constraint is the route.
    """
    vision_model(supports_vision=False)
    image = tmp_path / "chart.png"
    image.write_bytes(_png_bytes())

    result = _read(image, ctx)

    assert "switch to an image-capable model" in result.message


def test_an_unregistered_route_refuses_rather_than_assuming(tmp_path, ctx):
    """Unknown capability is not permission. Defaulting to "probably fine" is the bug.

    `supports_vision` defaults to False on `ModelConfig`, so a missing config is easy to
    treat as "just ask and see". That turns an unknown route into a paid failed call.
    """
    ctx.extra["model_name"] = "vendor/never-registered"
    image = tmp_path / "chart.png"
    image.write_bytes(_png_bytes())

    result = _read(image, ctx)

    assert result.success is False
    assert "not a registered model" in result.message


def test_an_image_capable_route_is_allowed_through(tmp_path, vision_model, ctx):
    """The gate must be a capability check, not a refusal with extra steps.

    A gate written against the wrong field, or inverted, passes every test above while
    making the tool unusable — and the tool still reports its failures honestly, so
    nothing else notices.
    """
    vision_model(supports_vision=True)
    image = tmp_path / "chart.png"
    image.write_bytes(_png_bytes())

    result = _read(image, ctx)

    assert result.success is True
    assert result.data["media_type"] == "image/png"


# --------------------------------------------------------------------------- #
# Admission
# --------------------------------------------------------------------------- #
def test_the_format_comes_from_the_bytes_not_the_extension(tmp_path, vision_model, ctx):
    """A `.png` full of something else is rejected by the provider, not by the extension.

    Trusting the name means the declared media type and the actual bytes disagree in the
    request. Anthropic and Google both key off the declared type, so the failure is a
    provider-side decode error that mentions neither the file nor this tool.
    """
    vision_model(supports_vision=True)
    liar = tmp_path / "screenshot.png"
    liar.write_bytes(b"this is plainly not an image")

    result = _read(liar, ctx)

    assert result.success is False
    assert "not a PNG, JPEG, GIF or WebP" in result.message


def test_reading_the_same_image_twice_leaves_one_live_attachment(tmp_path, vision_model, ctx):
    """Re-reading is cheap for the model and expensive for the request.

    Every live attachment is re-encoded into every later call. A model that reads the same
    screenshot in two steps — which it will, having forgotten — would otherwise spend the
    budget twice on identical bytes and fill the live set with one image.
    """
    vision_model(supports_vision=True)
    image = tmp_path / "chart.png"
    image.write_bytes(_png_bytes())

    _read(image, ctx)
    _read(image, ctx)

    assert len(attachment_manager.live(ctx.id)) == 1


def test_the_live_set_drops_the_oldest_image_not_the_newest(tmp_path, vision_model, ctx):
    """An unbounded live set is an unbounded surcharge on every remaining call.

    Which end to drop is a real choice: the newest is the one just asked for, so dropping
    it would silently discard the image the model is about to reason about, while the
    oldest has usually already answered its question.
    """
    vision_model(supports_vision=True)
    for index in range(attachment_manager.MAX_LIVE_IMAGES + 2):
        image = tmp_path / f"shot-{index}.png"
        # Distinct sizes so the content digests differ; identical bytes would dedupe.
        image.write_bytes(_png_bytes(width=2 + index, height=2))
        _read(image, ctx)

    live = attachment_manager.live(ctx.id)
    assert len(live) == attachment_manager.MAX_LIVE_IMAGES
    assert live[-1].source_path.endswith("shot-5.png")
    assert not any(item.source_path.endswith("shot-0.png") for item in live)


def test_an_image_over_the_size_limit_is_refused_with_the_number(
    tmp_path, vision_model, ctx, monkeypatch
):
    """Above the provider's ceiling the request is rejected, so refusing early is cheaper.

    The message has to carry the actual size and the limit: "too large" alone leaves the
    model guessing at how much to crop, and it will guess by trying again.
    """
    vision_model(supports_vision=True)
    monkeypatch.setattr(attachment_manager, "MAX_IMAGE_BYTES", 64)
    image = tmp_path / "huge.png"
    image.write_bytes(_png_bytes(width=64, height=64))

    result = _read(image, ctx)

    assert result.success is False
    assert "the limit is 64" in result.message


def test_a_storage_failure_raises_instead_of_reporting_success(tmp_path, monkeypatch):
    """Unlike the spill store next door, this one must not degrade quietly.

    `spill.save_text` returns None when it cannot write, on purpose: losing a
    transcript beats failing a command that already ran. Copying that here would put a
    reference in front of the model to bytes that are not on disk, and the failure would
    surface a call later with nothing pointing back to this one.
    """
    with pytest.raises(AttachmentError):
        attachment_manager.save_image(str(tmp_path / "absent.png"), session_key="s")


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def test_a_live_attachment_becomes_an_image_part_the_serializers_understand(tmp_path):
    """The part has to be a `data:` URL, not a path and not a `file://` URL.

    Three of the five serializers here forward the URL string to the provider untouched,
    so a local path arrives as a broken URL and fails there. Only the inline form works on
    every route, and the ones that do read the filesystem accept it too.
    """
    image = tmp_path / "chart.png"
    image.write_bytes(_png_bytes())
    attachment = attachment_manager.save_image(str(image), session_key="delivery")
    try:
        part = attachment_manager.content_part(attachment)
        assert isinstance(part, ContentPartImage)
        assert part.image_url.url.startswith("data:image/png;base64,")
        assert part.image_url.media_type == "image/png"
    finally:
        attachment_manager.release("delivery")


def test_the_turn_the_agent_builds_carries_the_live_images(tmp_path, vision_model, ctx):
    """Committing the bytes is half the job; a turn that does not carry them is the other.

    This is the step where "attached the screenshot" becomes true or stays a sentence. The
    tool result is a plain string, so nothing else in the pipeline would notice the
    difference between an image delivered and an image merely announced — and the model
    would go on to describe a picture it never saw.
    """
    from agentevolver.agent.types import Agent
    from agentevolver.message import HumanMessage

    vision_model(supports_vision=True)
    image = tmp_path / "chart.png"
    image.write_bytes(_png_bytes())
    _read(image, ctx)

    messages = Agent._with_attachments([HumanMessage(content="the turn so far")], ctx)

    assert len(messages) == 2
    assert messages[0].content == "the turn so far", "the existing turn must not be rewritten"
    images = [part for part in messages[1].content if isinstance(part, ContentPartImage)]
    assert len(images) == 1
    assert str(image) in messages[1].text


def test_a_run_with_no_images_gets_the_turn_it_would_have_had(tmp_path, ctx):
    """An empty live set must add nothing at all, not an empty trailing message.

    Every step goes through here. A stray extra user turn on the common path would shift
    the prompt for every agent in the framework, and an empty content list is rejected by
    some providers outright.
    """
    from agentevolver.agent.types import Agent
    from agentevolver.message import HumanMessage

    original = [HumanMessage(content="the turn so far")]
    assert Agent._with_attachments(original, ctx) is original


def test_the_stored_copy_is_read_back_not_the_original_file(tmp_path):
    """The model's view of an image must not change because the file did.

    A run that reads a plot, regenerates it, and then reasons about the first one is a run
    whose conclusion cannot be reproduced — and nothing in the transcript would show that
    the picture moved underneath it.
    """
    image = tmp_path / "chart.png"
    image.write_bytes(_png_bytes(width=2))
    attachment = attachment_manager.save_image(str(image), session_key="pinned")
    try:
        first = attachment_manager.content_part(attachment).image_url.url
        image.write_bytes(_png_bytes(width=9))
        assert attachment_manager.content_part(attachment).image_url.url == first
    finally:
        attachment_manager.release("pinned")
