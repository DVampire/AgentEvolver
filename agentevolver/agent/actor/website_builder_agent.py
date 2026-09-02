"""Dedicated website product engineer with MetaAgent orchestration mechanics."""

import json
from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.agent.actor.meta_agent import MetaAgent
from agentevolver.registry import AGENT

_MANIFEST_MARKER = "## runtime-input-manifest"


def bind_runtime_input_manifest(task: str, files: Optional[List[str]]) -> str:
    """Bind an optional, role-labelled attachment manifest to staged paths.

    Ordinary website tasks need no manifest. Launchers that require privacy-preserving
    role routing may provide an ``attachments`` list of any length; attachment staging
    happens after the launcher builds the task, so paths are rebound here without
    reading their contents.
    """
    attachments = [str(path) for path in (files or [])]
    before, marker, after = str(task).partition(_MANIFEST_MARKER)
    if not marker:
        return str(task)
    start = after.find("{")
    if start < 0:
        raise ValueError("runtime-input-manifest has no JSON object")
    try:
        manifest = json.loads(after[start:])
    except (TypeError, ValueError) as error:
        raise ValueError(f"runtime-input-manifest is invalid JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise ValueError("runtime-input-manifest must be a JSON object")
    declared = manifest.get("attachments")
    if not isinstance(declared, list):
        raise ValueError("runtime-input-manifest must contain an attachments list")
    if len(declared) != len(attachments):
        raise ValueError(
            "runtime-input-manifest attachment count does not match staged files: "
            f"declared={len(declared)}, staged={len(attachments)}"
        )
    rebound = []
    for index, (entry, staged_path) in enumerate(zip(declared, attachments)):
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("role"):
            raise ValueError(
                f"runtime-input-manifest attachment {index} requires id and role"
            )
        bound = dict(entry)
        bound.pop("source_path", None)
        bound["path"] = staged_path
        bound["staged"] = True
        rebound.append(bound)
    manifest["attachments"] = rebound
    manifest["paths_staged"] = True
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
    reserved for bounded specialist work, user research, and framework evolution.
    """

    name: str = Field(default="website_builder_agent")
    description: str = Field(
        default=(
            "An evolvable website product engineer that designs, implements, tests, "
            "deploys, and improves web products from task-defined requirements."
        )
    )
    metadata: Dict[str, Any] = Field(
        default_factory=lambda: {
            "role": "website_builder",
            "orchestrator": True,
            "product_engineering": True,
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
        """Bind optional staged roles, then use MetaAgent's ordinary event loop."""
        self._bind_runtime_environment(ctx)
        task = bind_runtime_input_manifest(task, files)
        return await super().on_start(task, files, ctx, ref, **kwargs)


__all__ = ["WebsiteBuilderAgent", "bind_runtime_input_manifest"]
