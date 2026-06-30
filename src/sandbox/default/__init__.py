"""Built-in sandbox backends. Importing this registers them with ``SANDBOX``."""

from .base import OpenSandbox
from .code_interpreter import CodeInterpreterSandbox
from .playwright import PlaywrightSandbox

__all__ = ["OpenSandbox", "CodeInterpreterSandbox", "PlaywrightSandbox"]
