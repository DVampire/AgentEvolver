"""What a malformed model-facing context raises.

Its own file because every other module in this package raises it, and importing the
whole builder to catch an error is how import cycles start.
"""

from __future__ import annotations


class ContextProtocolError(ValueError):
    """The model-facing context would violate the four-layer protocol."""


__all__ = ["ContextProtocolError"]
