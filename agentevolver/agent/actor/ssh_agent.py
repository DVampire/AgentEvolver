"""SSHAgent — works on a remote machine through the SSH environment, from the local one."""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from agentevolver.agent.env_binding import EnvironmentBound
from agentevolver.agent.types import Agent, AgentContext
from agentevolver.registry import AGENT
from agentevolver.response.types import Response


@AGENT.register_module(force=True)
class SSHAgent(EnvironmentBound, Agent):
    """An agent operating a remote host over SSH, while still standing on the local one.

    It keeps its local tools, which is the one place it deliberately parts company with
    ``BrowserAgent``. A browser agent has no use for a local shell; this one does — the
    common shape of the work is *prepare here, run there*: read a local file, `upload` it,
    `launch` the job, `logs` it back. Taking the local tools away would leave that first
    step with nothing to do it.

    Everything else is the standard loop. The only bespoke part is that the remote's state
    is observed before each step, the same way the browser's page is.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="ssh_agent")
    description: str = Field(
        default="An agent that operates a remote machine over SSH: running commands, "
                "managing long-running jobs, editing files and moving data, while "
                "keeping its local tools for the work that belongs on this side."
    )
    metadata: Dict[str, Any] = Field(default={})
    enable_evolving: bool = Field(default=False)

    def __init__(
        self,
        base_dir: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        model_name: Optional[str] = None,
        prompt_name: Optional[str] = None,
        memory_name: Optional[str] = None,
        env_name: str = "remote_host",
        max_actions: int = 5,
        max_step: int = 40,
        review_steps: int = 5,
        enable_evolving: bool = False,
        **kwargs,
    ):
        super().__init__(
            base_dir=base_dir,
            name=name,
            description=description,
            metadata=metadata,
            model_name=model_name,
            prompt_name=prompt_name or "ssh_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_step=max_step,
            review_steps=review_steps,
            enable_evolving=enable_evolving,
            **kwargs,
        )
        self.env_name = env_name

    async def _get_agent_context(
        self,
        task: str,
        step_number: int = 0,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Add the remote host's current state, refreshed every step.

        The agent is working somewhere it cannot see, and the far side changes without it:
        a launched job finishes, a disk fills, someone else takes the GPUs. Re-observing
        each step is what keeps its picture of the machine from going stale — the same
        reason the browser agent re-reads the page.
        """
        base = await super()._get_agent_context(task, step_number=step_number, ctx=ctx, **kwargs)
        state = await self._observe(ctx)
        base["remote_state"] = (
            state.get("state") if state else "[Remote state unavailable — the host may be "
                                             "unreachable; try a `run` to confirm.]"
        )
        base.update(await self._get_environment_context())
        return base

    async def __call__(
        self,
        task: Optional[str] = None,
        files: Optional[List[str]] = None,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Response:
        """Entry point — runs the base-class standard think-and-act loop unchanged."""
        return await super().__call__(task=task, files=files, ctx=ctx, **kwargs)
