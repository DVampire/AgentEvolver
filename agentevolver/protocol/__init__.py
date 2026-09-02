"""Protocol layer — typed channels for agent-to-agent interaction, on top of the runtime.

runtime = how messages move; protocol = the shape and session scoping of each conversation.
One ``protocol_manager`` exposes every channel while Runtime owns live ref controls,
subscription state, and serialized delivery.
"""

from agentevolver.protocol.server import ProtocolManager, protocol_manager
from agentevolver.protocol.types import (
    ControlMessage,
    EscalationMessage,
    MonitorProgressMessage,
    QueryMessage,
    SubscriptionEventMessage,
)

__all__ = [
    "protocol_manager",
    "ProtocolManager",
    "EscalationMessage",
    "MonitorProgressMessage",
    "ControlMessage",
    "QueryMessage",
    "SubscriptionEventMessage",
]
