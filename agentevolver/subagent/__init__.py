"""Delegate a child agent without sitting through it — and keep talking to it."""

from .server import SubagentServer, subagent_manager
from .types import ChildState, Subagent

__all__ = [
    "SubagentServer",
    "subagent_manager",
    "Subagent",
    "ChildState",
]
