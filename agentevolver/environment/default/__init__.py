"""The shipped ECP environments.

Every entry here is an `Environment` subclass with `@action`-registered actions and an
`ENVIRONMENT.md` beside it. Something with no actions is not an environment and does not
belong here: listing one implies an environment that `environment_manager.list()` never
returns, which is how a name reaches the model that nothing can dispatch.
"""

from .browser.environment import BrowserEnvironment
from .artifact_renderer.environment import ArtifactRendererEnvironment
from .computer.environment import ComputerEnvironment
from .ssh.environment import SSHEnvironment
from .terminal.environment import TerminalEnvironment

__all__ = [
    "ArtifactRendererEnvironment",
    "BrowserEnvironment",
    "ComputerEnvironment",
    "SSHEnvironment",
    "TerminalEnvironment",
]
