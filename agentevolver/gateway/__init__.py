"""Versioned transport boundary for interactive AgentEvolver clients."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentevolver.gateway.service import AgentGateway


def __getattr__(name: str):
    if name == "AgentGateway":
        from agentevolver.gateway.service import AgentGateway

        return AgentGateway
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["AgentGateway"]
