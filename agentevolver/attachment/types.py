"""What an attachment is: bytes the model saw, pinned away from the file they came from."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.message.types import SupportedImageMediaType

#: Every format all five provider serializers can carry, mapped from its file signature.
#: The signature is the authority, never the extension: a `.png` holding JPEG bytes is
#: how a request gets rejected by the provider with an error that names neither the file
#: nor the tool that read it.
IMAGE_SIGNATURES: tuple[tuple[bytes, int, SupportedImageMediaType], ...] = (
    (b"\x89PNG\r\n\x1a\n", 0, "image/png"),
    (b"\xff\xd8\xff", 0, "image/jpeg"),
    (b"GIF87a", 0, "image/gif"),
    (b"GIF89a", 0, "image/gif"),
    (b"WEBP", 8, "image/webp"),
)


class ImageAttachment(BaseModel):
    """One image committed to the store, and everything needed to re-send it.

    ``attachment_id`` is the digest of the bytes, so reading the same file twice yields
    one attachment rather than two copies of it. ``source_path`` is kept for the human
    reading a trace and for the tool's own reply; nothing resolves it again, because the
    file is free to change afterwards and the model's view must not.
    """

    model_config = ConfigDict(extra="forbid")

    attachment_id: str = Field(description="``sha256:<hex>`` over the exact stored bytes.")
    media_type: SupportedImageMediaType = Field(description="Format read from the bytes, not the extension.")
    byte_count: int = Field(description="Size of the stored image in bytes.")
    source_path: str = Field(description="Absolute path the image was read from, for the reply and the trace.")
    locator: str = Field(description="Absolute path of the stored copy. Descriptive; the store resolves ids, not paths.")


__all__ = ["IMAGE_SIGNATURES", "ImageAttachment"]
