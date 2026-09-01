"""Dedicated product engineer for participatory website evolution."""

import json
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.registry import AGENT

_MANIFEST_MARKER = "## runtime-input-manifest"


def bind_runtime_input_manifest(task: str, files: Optional[List[str]]) -> str:
    """Replace launcher source paths with the session-staged attachment paths.

    Attachment staging happens inside AgentManager, after the launcher built the task.
    Binding here keeps the role-only manifest truthful without reading persona content.
    """
    attachments = [str(path) for path in (files or [])]
    if len(attachments) != 4:
        raise ValueError(
            "website evolution requires four staged attachments in role order: "
            "site brief, persona_01, persona_02, persona_03"
        )
    before, marker, after = str(task).partition(_MANIFEST_MARKER)
    if not marker:
        raise ValueError("website evolution task is missing runtime-input-manifest")
    start = after.find("{")
    if start < 0:
        raise ValueError("runtime-input-manifest has no JSON object")
    try:
        manifest = json.loads(after[start:])
    except (TypeError, ValueError) as error:
        raise ValueError(f"runtime-input-manifest is invalid JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise ValueError("runtime-input-manifest must be a JSON object")

    manifest.update(
        {
            "site_brief": attachments[0],
            "persona_01": attachments[1],
            "persona_02": attachments[2],
            "persona_03": attachments[3],
            "paths_staged": True,
        }
    )
    explanation = after[:start].strip()
    return (
        f"{before.rstrip()}\n\n{_MANIFEST_MARKER}\n"
        f"{explanation}\n"
        f"{json.dumps(manifest, ensure_ascii=False, indent=2)}"
    )


@AGENT.register_module(force=True)
class WebsiteBuilderAgent(MetaAgent):
    """Direct website builder with MetaAgent's reusable event lifecycle.

    This is a distinct registered Agent rather than a renamed MetaAgent configuration:
    it has its own runtime identity, version history, prompt default, permissions, memory,
    traces, and evolution target. Product engineering stays in this agent; delegation is
    reserved for persistent user co-design and framework evolution.
    """

    name: str = Field(default="website_builder_agent")
    description: str = Field(
        default=(
            "An evolvable website product engineer that builds and deploys releases, "
            "coordinates persistent user co-designers, delivers personal and shared "
            "contributions, and evolves missing capabilities with rollback gates."
        )
    )
    metadata: Dict[str, Any] = Field(
        default_factory=lambda: {
            "role": "website_builder",
            "orchestrator": True,
            "participatory_design": True,
        }
    )
    enable_evolving: bool = Field(default=True)

    def __init__(
        self,
        base_dir: str,
        prompt_name: Optional[str] = None,
        max_step: int = 180,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            base_dir=base_dir,
            prompt_name=prompt_name or "website_builder_agent",
            max_step=max_step,
            **kwargs,
        )

    @staticmethod
    def _bind_runtime_environment(ctx: Any) -> None:
        """Mount only the coordinator environment on the Builder itself."""
        # BrowserEnvironment is registered globally because Website User children need
        # it. The Builder itself coordinates those children and only works in JobEnvironment;
        # mounting the browser here creates an unused page and pushes critical schemas into
        # deferred discovery. Keep the runtime boundary explicit before the first prompt.
        if ctx is not None and getattr(ctx, "extra", None) is not None:
            ctx.extra["environment_allowlist"] = ["job"]

    async def on_start(self, task, files, ctx, ref, **kwargs):
        """Bind staged role paths, then use MetaAgent's ordinary event-driven loop."""
        self._bind_runtime_environment(ctx)
        task = bind_runtime_input_manifest(task, files)
        return await super().on_start(task, files, ctx, ref, **kwargs)


__all__ = ["WebsiteBuilderAgent", "bind_runtime_input_manifest"]
