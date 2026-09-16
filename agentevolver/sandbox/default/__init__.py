"""Built-in sandbox backends. Importing this registers them with ``SANDBOX``."""

from .base import OpenSandbox
from .chrome_vnc import ChromeVncSandbox
from .computer import DesktopSandbox
from .docker import DockerSandbox
from .harbor import HarborSandbox
from .host import HostSandbox
from .playwright import PlaywrightSandbox
from .vscode import VscodeSandbox

__all__ = [
    "OpenSandbox", "DockerSandbox", "PlaywrightSandbox", "HarborSandbox",
    "ChromeVncSandbox", "VscodeSandbox", "DesktopSandbox", "HostSandbox",
]
