"""A solo game developer using the shared plan, capability and evolution lifecycle."""

import hashlib
from typing import Any, Dict, List

from pydantic import Field, PrivateAttr

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
    continue_from: str = Field(default="", description="Stopped session whose game files and plan seed this new run.")
    plan_refresh_steps: int = Field(default=3, ge=1)
    _plan_digest: str = PrivateAttr(default="")
    _plan_updated_step: int = PrivateAttr(default=0)
    _plan_reconcile: bool = PrivateAttr(default=False)
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
        if self.continue_from:
            from agentevolver.paths import path_manager

            from .game_continuation import seed_game_session

            roots = path_manager.session_roots()
            seed_game_session(self.continue_from, roots["workspace"], roots["plan"])
            self._plan_reconcile = True
            plan = roots["plan"] / "plan.md"
            if plan.is_file():
                self._plan_digest = hashlib.sha256(plan.read_bytes()).hexdigest()
        environment = await environment_manager.get("godot_environment")
        if environment is None:
            raise RuntimeError("GameBuilder requires godot_environment")
        result = await environment.prepare_workspace(ctx=self.ctx)
        if not result["success"]:
            raise RuntimeError(result["message"])

    async def on_step(self, step):
        from agentevolver.plan.server import plan_manager, plan_path
        from agentevolver.plan.types import PlanMode

        note = await super().on_step(step)
        sid = str(getattr(self.ctx, "id", "") or "")
        if not self.use_plan or plan_manager.mode(sid) is PlanMode.OFF:
            return note
        path = plan_path(sid)
        content = path.read_bytes() if path.is_file() else b""
        digest = hashlib.sha256(content).hexdigest()
        if content and digest != self._plan_digest:
            self._plan_digest, self._plan_updated_step = digest, step
            self._plan_reconcile = False
        if not content or self._plan_reconcile or step - self._plan_updated_step >= self.plan_refresh_steps:
            reason = ("Reconcile the carried plan with actual source and historical evidence first."
                      if self._plan_reconcile else
                      "The plan is missing or has not changed during recent steps.")
            note += (f"\n<game-plan-update-required>\n{reason}\n"
                     f"Update {path} before starting another implementation item. The task HTML is an outline; "
                     "author concrete story beats, character motives, quest branches, gameplay rules, "
                     "scene/module design and acceptance checks. Record work IDs, changed files, actual "
                     "results, blockers and next steps; distinguish designed, implemented, verified and played. "
                     "Summarize long narrative/design documents here with links. Do not just touch the file "
                     "or invent progress. This reminder does not change an active plan review gate.\n"
                     "</game-plan-update-required>")
        return note

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
