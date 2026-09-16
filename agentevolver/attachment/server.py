"""The attachment manager: commit an image, then keep handing it back every turn."""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Dict, List, Optional

from agentevolver.attachment.types import IMAGE_SIGNATURES, ImageAttachment
from agentevolver.logger import logger
from agentevolver.message.types import ContentPartImage, ImageURL, SupportedImageMediaType
from agentevolver.paths import P, path_manager


class AttachmentError(Exception):
    """The image could not be admitted. Carries the sentence the model is shown."""


class AttachmentManagerServer:
    """Commit image bytes to disk and remember which ones a run is currently showing.

    Two jobs that have to stay together. Committing pins the bytes: the model's view of
    an image must not change because the file on disk did, and a run that reads a
    screenshot, regenerates it, and reasons about the first one is a run whose conclusion
    cannot be reproduced. Remembering is what makes the image survive past the step that
    read it — this framework rebuilds its whole prompt every step from memory, so an
    image appended once is gone by the next request unless something re-attaches it.

    ``live`` is bounded on purpose. Every attachment is re-encoded into every later
    request, so an unbounded list is an unbounded and permanent addition to each call.
    The oldest is dropped, not the newest, because the reason to read a second image is
    usually that the first one answered its question.
    """

    #: How many images one run keeps in context at once. Matches ``BrowserAgent``'s
    #: screenshot budget, which is the only other place in this repo that puts images in
    #: front of a model and the only calibration available.
    MAX_LIVE_IMAGES = 4

    #: Per-image ceiling. 5 MB is the smallest limit among the providers this repo
    #: serializes for; above it the request is rejected by the provider, so refusing here
    #: turns a failed call into a message the model can act on.
    MAX_IMAGE_BYTES = 5 * 1024 * 1024

    def __init__(self) -> None:
        self._live: Dict[str, List[ImageAttachment]] = {}

    # ------------------------------------------------------------------
    # Admission
    # ------------------------------------------------------------------
    @staticmethod
    def detect_media_type(data: bytes) -> Optional[SupportedImageMediaType]:
        """Return the media type the bytes themselves declare, or None."""
        for signature, offset, media_type in IMAGE_SIGNATURES:
            if data[offset:offset + len(signature)] == signature:
                return media_type
        return None

    def save_image(self, path: str, *, session_key: str) -> ImageAttachment:
        """Commit the image at ``path`` and make it live for ``session_key``.

        Raises rather than degrading, unlike the spill store next door. A spilled
        transcript that failed to save costs a transcript; an attachment that failed to
        save and was reported as saved puts a reference in front of the model to bytes
        that are not there, and the call that follows fails somewhere with no mention of
        this one.

        Raises:
            AttachmentError: The file is unreadable, too large, or not an image format
                every provider can carry.
        """
        try:
            byte_count = os.path.getsize(path)
        except OSError as error:
            raise AttachmentError(f"Cannot read {path}: {error}") from error
        if byte_count > self.MAX_IMAGE_BYTES:
            raise AttachmentError(
                f"{path} is {byte_count:,} bytes; the limit is {self.MAX_IMAGE_BYTES:,}. "
                f"Resize or crop it first."
            )
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as error:
            raise AttachmentError(f"Cannot read {path}: {error}") from error

        media_type = self.detect_media_type(data)
        if media_type is None:
            raise AttachmentError(
                f"{path} is not a PNG, JPEG, GIF or WebP image — those are the formats "
                f"every model route here can accept."
            )

        digest = hashlib.sha256(data).hexdigest()
        root = path_manager.get(P.ATTACHMENTS)
        directory = root / digest[:2]
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stored = directory / digest
        if not stored.exists():
            # 'x' rather than 'w': a path that already exists is either the same content
            # (nothing to do) or a planted symlink, and neither is a reason to write.
            descriptor = os.open(stored, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)

        attachment = ImageAttachment(
            attachment_id=f"sha256:{digest}",
            media_type=media_type,
            byte_count=byte_count,
            source_path=os.path.abspath(path),
            locator=str(stored),
        )
        self._register(session_key, attachment)
        logger.info(f"| 🖼️ Attached {media_type} ({byte_count:,} bytes) from {path}")
        return attachment

    # ------------------------------------------------------------------
    # Live set
    # ------------------------------------------------------------------
    def _register(self, session_key: str, attachment: ImageAttachment) -> None:
        """Make one attachment live, replacing an earlier read of the same bytes."""
        live = self._live.setdefault(session_key, [])
        live[:] = [item for item in live if item.attachment_id != attachment.attachment_id]
        live.append(attachment)
        del live[:-self.MAX_LIVE_IMAGES]

    def live(self, session_key: str) -> List[ImageAttachment]:
        """The attachments this run is currently showing, oldest first."""
        return list(self._live.get(session_key, ()))

    def release(self, session_key: str) -> None:
        """Forget a run's live set. The committed bytes stay where they are."""
        self._live.pop(session_key, None)

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------
    def content_part(self, attachment: ImageAttachment) -> ContentPartImage:
        """Render one attachment as the message part the provider serializers consume.

        A ``data:`` URL rather than a path or a ``file://`` URL: the OpenAI, OpenRouter
        and LLM-Hub serializers forward whatever string they are given straight to the
        provider, so anything that needs the local filesystem to make sense arrives as a
        broken URL. Only the inline form works on every route.
        """
        with open(attachment.locator, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        return ContentPartImage(
            image_url=ImageURL(
                url=f"data:{attachment.media_type};base64,{encoded}",
                media_type=attachment.media_type,
            )
        )


#: Global attachment manager instance
attachment_manager = AttachmentManagerServer()

__all__ = ["AttachmentError", "AttachmentManagerServer", "attachment_manager"]
