"""Central port registry: named defaults + a persisted host-port allocator."""

from .server import (
    CHROME_CDP,
    GATEWAY,
    NOVNC,
    OPENSANDBOX,
    TRACE_UI,
    VNC,
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
    "CHROME_CDP",
    "VNC",
    "NOVNC",
]
