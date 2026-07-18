"""Docker sandbox manager (scaffold — not yet implemented).

Sibling of ``agentevolver/sandbox/`` (OpenSandbox). When implemented, this module will
back the same :class:`~agentevolver.sandbox.types.Sandbox` contract with a plain Docker
runtime (containers + exec + file copy), and register its handles with the
``DOCKER`` registry.

TODO: implement DockerSandbox(Sandbox) in agentevolver/docker/default/ and wire acquire().
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class DockerManagerServer(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    async def initialize(self) -> None:  # pragma: no cover - scaffold
        return None

    async def acquire(self, *args: Any, **kwargs: Any):  # pragma: no cover - scaffold
        raise NotImplementedError(
            "agentevolver.docker is a scaffold. Implement DockerSandbox in agentevolver/docker/default/ first. "
            "Use agentevolver.sandbox (OpenSandbox) for now."
        )

    async def cleanup(self) -> None:  # pragma: no cover - scaffold
        return None


docker_manager = DockerManagerServer()
