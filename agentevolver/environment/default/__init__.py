"""The shipped ECP environments.

Every entry here is an `Environment` subclass with `@action`-registered actions and an
`ENVIRONMENT.md` beside it. Execution-world providers are not environments and live at
`environment/world_*.py` — `E2BExecutionWorld` was here once, and being listed among
these implied a fifth environment that `environment_manager.list()` never returns.
"""

from .browser.environment import BrowserEnvironment
from .artifact_renderer.environment import ArtifactRendererEnvironment
from .computer.environment import ComputerEnvironment
from .ssh.environment import SSHEnvironment

__all__ = [
    "ArtifactRendererEnvironment",
    "BrowserEnvironment",
    "ComputerEnvironment",
    "SSHEnvironment",
]
