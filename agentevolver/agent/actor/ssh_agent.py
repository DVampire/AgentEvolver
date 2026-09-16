"""SSHAgent — works on a remote machine through the SSH environment, from the local one."""

from typing import Any, Dict, List

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent
from agentevolver.environment.server import environment_manager
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class SSHAgent(Agent):
    """An agent that operates a remote machine through the SSH environment's actions.

    Two sets of names, and the split is the point. The environment's actions — `run`,
    `read`, `edit`, `launch` — act on the remote machine; the ordinary tools act on this
    one. No action does both and no argument moves a tool between them, so a step cannot
    read on one machine and execute on the other by accident. Data crosses only through
    `upload` and `download`.

    Everything else is the standard loop. The base class already re-reads every mounted
    environment before each step, which is what keeps the agent's picture of a machine it
    cannot see from going stale, so all this class adds is one sentence for the case where
    the read comes back empty.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="ssh_agent")
    description: str = Field(
        default="An agent that operates a remote machine over SSH: running commands, "
        "managing long-running jobs, editing files and moving data through its "
        "environment actions, while its ordinary tools act on the local machine."
    )
    metadata: Dict[str, Any] = Field(default={})
    prompt_name: str = Field(default="ssh_agent")
    max_actions: int = Field(default=5)
    max_step: int = Field(default=40)
    enable_evolving: bool = Field(default=False)
    env_names: List[str] = Field(default=["remote_host"])

    async def on_start(self, task: str, proc: Any) -> None:
        """Fail loudly when the environment this agent is named for is not mounted.

        A mismatch between this declaration and the config's ``env_names`` used to degrade
        silently: state came back empty and the prompt block was simply absent, so the
        agent behaved like an SSH agent with no machine instead of reporting that it had
        none.
        """
        for name in self.env_names:
            if await environment_manager.get_info(name) is None:
                raise RuntimeError(
                    f"environment {name!r} is not registered; "
                    f"add it to this config's `env_names`"
                )

    async def environment_state(self, ctx: Any) -> str:
        """The remote's state, with a next step when the read comes back empty.

        An unreachable host is not a missing feature. An agent told only "unavailable"
        re-reads the state; told to try a `run`, it tests the connection.
        """
        state = await super().environment_state(ctx)
        if state:
            return state
        return (
            "<environment name=\"remote_host\">\n"
            "[Remote state unavailable — the host may be unreachable; try a `run` to "
            "confirm.]\n</environment>"
        )


__all__ = ["SSHAgent"]
