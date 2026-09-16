"""Remote-host environment: operate another machine over one persistent SSH connection."""

from .environment import SSHEnvironment
from .service import SSHConfig, SSHResult, SSHService, RemotePathError

__all__ = ["SSHEnvironment", "SSHConfig", "SSHResult", "SSHService", "RemotePathError"]
