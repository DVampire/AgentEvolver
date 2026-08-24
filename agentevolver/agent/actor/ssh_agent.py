"""SSHAgent — works on a remote machine through the SSH environment, from the local one."""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent, AgentContext
from agentevolver.environment.server import environment_manager
from agentevolver.registry import AGENT
from agentevolver.response.types import Response


@AGENT.register_module(force=True)
class SSHAgent(Agent):
    """An agent that operates a remote machine through the SSH environment's actions.

    Two sets of names, and the split is the point. The environment's actions — `run`,
    `read`, `edit`, `launch` — act on the remote machine; the ordinary tools act on this
    one. No action does both and no argument moves a tool between them, so a step cannot
    read on one machine and execute on the other by accident. Data crosses only through
    `upload` and `download`.

    Everything else is the standard loop. The only bespoke part is that the remote's state
    is observed before each step, the same way the browser's page is.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="ssh_agent")
    description: str = Field(
        default="An agent that operates a remote machine over SSH: running commands, "
                "managing long-running jobs, editing files and moving data through its "
                "environment actions, while its ordinary tools act on the local machine."
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
        return await super()._get_agent_context(task, step_number=step_number, ctx=ctx, **kwargs)

    async def _get_environment_context(self, ctx) -> Dict[str, Any]:
        """The base class reads every mounted environment; this one adds what to do about it.

        Fetching used to happen in `_get_agent_context` here and in `BrowserAgent`, because
        the base class had no environment method at all. It has one now and it runs *after*
        `_get_agent_context`, so an override there would be overwritten — and, for the
        browser, would cost a second screenshot to produce the value being discarded.

        What is kept is the sentence: an unreachable host is not a missing feature, and an
        agent told only "unavailable" re-reads the state instead of testing the connection.
        """
        slots = await super()._get_environment_context(ctx)
        if not slots.get("environment_state"):
            slots["environment_state"] = (
                "[Remote state unavailable — the host may be unreachable; try a `run` "
                "to confirm.]"
            )
        return slots

    async def __call__(
        self,
        task: Optional[str] = None,
        files: Optional[List[str]] = None,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Response:
        """Entry point — runs the base-class standard think-and-act loop unchanged."""
        return await super().__call__(task=task, files=files, ctx=ctx, **kwargs)

    async def on_start(
        self, task: str, files: Optional[List[str]], ctx: Optional[AgentContext], ref: Any,
        **kwargs,
    ) -> Optional[Response]:
        """Select the SSH provider before the first prompt or ToolContext is built."""
        if ctx is None:
            ctx = AgentContext()
        # `env_name` and the config's `env_names` name the same environment in two places,
        # and nothing else makes them agree. A mismatch degrades silently: state comes back
        # empty and the prompt block is absent, so the agent behaves like an SSH agent with
        # no machine rather than reporting that it has none.
        if await environment_manager.get_info(self.env_name) is None:
            raise RuntimeError(
                f"environment {self.env_name!r} is not registered; "
                f"add it to this config's `env_names`"
            )
        return await super().on_start(task=task, files=files, ctx=ctx, ref=ref, **kwargs)
