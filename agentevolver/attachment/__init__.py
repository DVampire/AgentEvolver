"""Commit an image the agent read, and keep showing it to the model afterwards."""

from .server import AttachmentError, AttachmentManagerServer, attachment_manager
from .types import IMAGE_SIGNATURES, ImageAttachment

__all__ = [
    "AttachmentError",
    "AttachmentManagerServer",
    "attachment_manager",
    "IMAGE_SIGNATURES",
    "ImageAttachment",
]
