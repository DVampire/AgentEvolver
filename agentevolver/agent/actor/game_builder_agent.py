"""A solo game developer using the shared plan, capability and evolution lifecycle."""

from typing import Any, Dict, List

from pydantic import Field

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.registry import AGENT


@AGENT.register_module(force=True)
class GameBuilderAgent(MetaAgent):
    name: str = Field(default="game_builder_agent")
    description: str = Field(default=(
        "A solo Godot game developer that designs, builds, plays and improves games, "
        "and evaluates reusable agent capabilities when actual development exposes a gap."
    ))
    metadata: Dict[str, Any] = Field(default={"role": "game_builder", "game_engine": "godot"})
    prompt_name: str = Field(default="game_builder_agent")
    include_agents: bool = Field(default=False)
    enable_evolving: bool = Field(default=True)
    max_step: int = Field(default=400)
    env_names: List[str] = Field(default_factory=lambda: ["godot_environment"])
    capability_allowlists: Dict[str, List[str]] = Field(default_factory=lambda: {
        "environment": ["godot_environment"], "agent": [],
    })

    async def on_start(self, task, proc):
        from agentevolver.environment.server import environment_manager

        # Kernel starts lifecycle hooks before Agent._run assigns self.ctx.
        # Bind the process context now so preparation, actions and early cleanup
        # all own the same session instead of creating a second default runtime.
        self.ctx = proc.ctx
        await super().on_start(task, proc)
        environment = await environment_manager.get("godot_environment", ctx=self.ctx)
        if environment is None:
            raise RuntimeError("GameBuilder requires godot_environment")
        result = await environment.prepare_workspace(ctx=self.ctx)
        if not result["success"]:
            raise RuntimeError(result["message"])



__all__ = ["GameBuilderAgent"]
