"""Protocol layer — typed channels for agent-to-agent interaction, on top of the runtime.

runtime = how messages move; protocol = the shape of each conversation. One
``protocol_manager`` exposes every channel (escalation / delegation / progress / control /
query / pubsub); message types live in ``types``.
"""

from agentevolver.protocol.server import ProtocolManager, protocol_manager
from agentevolver.protocol.types import (
    EscalationMessage,
    MonitorProgressMessage,
    ControlMessage,
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
