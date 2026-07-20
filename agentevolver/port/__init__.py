"""Central port registry: named defaults + a persisted host-port allocator."""

from .server import (
    GATEWAY,
    OPENSANDBOX,
    TRACE_UI,
    PortManager,
    is_free,
    os_free_port,
    port_manager,
)

__all__ = [
    "port_manager",
    "PortManager",
    "is_free",
    "os_free_port",
    "GATEWAY",
    "OPENSANDBOX",
    "TRACE_UI",
]
