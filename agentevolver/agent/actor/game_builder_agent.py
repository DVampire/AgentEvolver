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

        await super().on_start(task, proc)
        environment = await environment_manager.get("godot_environment")
        if environment is None:
            raise RuntimeError("GameBuilder requires godot_environment")
        result = await environment.prepare_workspace(ctx=self.ctx)
        if not result["success"]:
            raise RuntimeError(result["message"])

    def attachments(self):
        from agentevolver.message.types import (
            ContentPartImage,
            ContentPartText,
            HumanMessage,
            ImageURL,
        )

        observation = self._environment_observations.get("godot_environment") or {}
        shots = (observation.get("extra") or {}).get("screenshots") or []
        result = super().attachments()
        if shots:
            shot = shots[-1]
            result.append(HumanMessage(content=[
                ContentPartText(text=shot.screenshot_description),
                ContentPartImage(image_url=ImageURL(
                    url=f"data:image/png;base64,{shot.screenshot}", media_type="image/png")),
            ]))
        return result

    async def on_exit(self, status):
        from agentevolver.environment.server import environment_manager
        from agentevolver.logger import logger

        try:
            for name in ("godot_environment",):
                if name in self.env_names:
                    try:
                        environment = await environment_manager.get(name)
                        if environment is not None:
                            await environment.close_session(str(getattr(self.ctx, "id", "") or "default"))
                    except Exception as error:
                        logger.warning(f"GameBuilder could not close {name}: {error}")
        finally:
            await super().on_exit(status)


__all__ = ["GameBuilderAgent"]
