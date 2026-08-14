"""Park an oversized tool result on disk and hand the agent a way back to it."""

from .server import SpillManagerServer, spill_manager
from .types import SpillRef, SpillSource, SpillStore

__all__ = [
    "SpillManagerServer",
    "spill_manager",
    "SpillRef",
    "SpillSource",
    "SpillStore",
]
