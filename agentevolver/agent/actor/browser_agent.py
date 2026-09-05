"""BrowserAgent — drives a browser through its environment's actions.

An observe-act loop: read the page, plan against exactly what was read, act. The reading
is the only thing this agent needs that a leaf agent does not, and the base class already
re-reads every mounted environment before each step — so what is left here is caching
that one read so the screenshots attached to the prompt describe the same page the plan
was made against, and classifying the failures a browser produces.

It used to carry its own copy of the whole loop, because the previous base class had no
environment step and no attachment hook. Both exist now, so the copy is gone.
"""

from hashlib import sha256
from typing import Any, Dict, List, Literal

from pydantic import ConfigDict, Field

from agentevolver.agent.loop.decision import ActionResult, Decision
from agentevolver.agent.types import Agent
from agentevolver.environment.server import environment_manager
from agentevolver.logger import logger
from agentevolver.message.types import (
    ContentPartImage,
    ContentPartText,
    HumanMessage,
    ImageURL,
    Message,
)
from agentevolver.registry import AGENT
from agentevolver.response.types import Response


@AGENT.register_module(force=True)
class BrowserAgent(Agent):
    """An agent that operates a web browser through its environment's actions."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="browser_agent")
    description: str = Field(
        default="An agent that operates a web browser: navigating, reading pages, "
        "filling forms and clicking through flows using its environment's actions."
    )
    metadata: Dict[str, Any] = Field(default={})
    prompt_name: str = Field(default="browser_agent")
    max_actions: int = Field(default=3)
    max_step: int = Field(default=40)
    enable_evolving: bool = Field(default=False)
    env_names: List[str] = Field(default=["browser_environment"])
    #: Current page normally suffices; include the previous action on a failure.
    #: Visual-comparison workloads can explicitly request both every step.
    max_screenshots: int = Field(default=2, ge=0)
    screenshot_history: Literal["on_error", "always"] = "on_error"
    #: The minimal contract a browser worker runs under: it acts through environment
    #: actions, so the only tool it needs is the one that ends the run.
    capability_allowlists: Dict[str, List[str]] = Field(default={
        "tool": ["done_tool"],
        "skill": [],
        "connector": [],
        "plugin": [],
        "workflow": [],
    })
    #: Tools evolved during a run join this roster; nothing else does.
    #:
    #: A browser agent judges a product the way a person would, so it has to keep
    #: reaching it only through the page — no shell, no workspace, and no skill that
    #: would let it read the source instead of using the thing. Those stay excluded.
    #:
    #: A tool is the exception because the gap it closes here is real and cannot be
    #: closed any other way. This agent found that a release's video would not play and
    #: could not say how long that video was, what codec it used, or how large it was:
    #: it has no shell, so `ffprobe` existing on the host means nothing to it, and it had
    #: to take the builder's word — which its own instructions tell it never to do. A run
    #: that evolves an instrument for exactly that can hand it over, and does not have to
    #: know this process is listening in order to.
    accepts_evolved: List[str] = Field(default=["tool"])

    def __init__(self, base_dir: str = "", **kwargs: Any) -> None:
        super().__init__(base_dir=base_dir, **kwargs)
        #: The page this step planned against, kept so the attached screenshots and the
        #: rendered state describe the same observation rather than two reads a moment
        #: apart — for a browser, the second read costs another screenshot of a page the
        #: plan was not made against.
        self._observed: Dict[str, Any] = {}
        self._failures: Dict[str, int] = {}
        self._samples: Dict[str, str] = {}
        self._action_failed = False

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    @property
    def env_name(self) -> str:
        """The single environment this agent drives."""
        return self.env_names[0] if self.env_names else "browser_environment"

    async def environment_state(self, ctx: Any) -> str:
        """Read the page once per step, and hold it for the attachments."""
        try:
            self._observed = await environment_manager.get_state(
                self.env_name, ctx=ctx,
            ) or {}
        except Exception as error:  # noqa: BLE001 - a dead browser is a fact, not a stop
            logger.warning(f"| ⚠️ [{self.name}] could not read the browser: {error}")
            self._observed = {}
        state = self._observed.get("state") if isinstance(self._observed, dict) else None
        body = state or "[Environment state unavailable.]"
        return f'<environment name="{self.env_name}">\n{body}\n</environment>'

    def attachments(self) -> List[Message]:
        """The screenshots from this step's observation, so a vision model can ground.

        Select whole images, never crop/downsample them. Originals remain in the browser
        artifacts. Content identity avoids losing different pathless/overwritten images.
        """
        extra = (self._observed or {}).get("extra") or {}
        shots = extra.get("screenshots") or []
        unique, seen = [], set()
        for shot in reversed(shots):
            encoded = getattr(shot, "screenshot", None)
            if not encoded:
                continue
            digest = sha256(encoded.encode("ascii")).digest()
            if digest in seen:
                continue
            seen.add(digest)
            unique.append(shot)
        unique.reverse()
        limit = self.max_screenshots
        if self.screenshot_history == "on_error" and not self._action_failed:
            limit = min(limit, 1)
        if not unique or limit == 0:
            return []

        parts: List[Any] = []
        for shot in unique[-limit:]:
            encoded = getattr(shot, "screenshot", None)
            if not encoded:
                continue
            label = getattr(shot, "screenshot_description", "") or "Screenshot"
            parts.append(ContentPartText(text=f"\n[{label}]"))
            parts.append(ContentPartImage(image_url=ImageURL(
                url=f"data:image/png;base64,{encoded}", media_type="image/png",
            )))
        return [HumanMessage(content=parts)] if parts else []

    # ------------------------------------------------------------------
    # Failures
    # ------------------------------------------------------------------

    async def act(self, decision: Decision) -> List[ActionResult]:
        """Run the batch, and tally what kind of browser failure came back.

        Counted here rather than at the end because the categories are what a later run
        or an optimizer reads: "the model wrote the wrong dialect of Playwright" and "the
        page was covered by an overlay" call for different fixes, and both arrive as an
        ordinary action error.
        """
        results = await super().act(decision)
        self._action_failed = any(result.error for result in results)
        for result in results:
            if not result.error:
                continue
            category = self.classify(result.error)
            self._failures[category] = self._failures.get(category, 0) + 1
            self._samples.setdefault(category, str(result.error))
        return results

    @staticmethod
    def classify(message: str) -> str:
        """Which kind of browser failure this is."""
        text = str(message or "").lower()
        if any(marker in text for marker in (
            "accepts async playwright python", "invalid syntax", ".slice",
            "unexpected token", "is not defined",
        )):
            return "browser_command_language"
        if "timed out" in text or "timeout" in text:
            return "browser_command_timeout"
        if "intercepts pointer events" in text:
            return "browser_interaction_blocked"
        return "browser_action_failure"

    async def finalize(self, response: Response) -> Response:
        """Carry the failure tally out with the result."""
        data = dict(response.data or {})
        data["diagnostics"] = {
            "action_error_total": sum(self._failures.values()),
            "action_error_counts": dict(self._failures),
            "action_error_samples": dict(self._samples),
        }
        return response.model_copy(update={"data": data})

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    async def on_start(self, task: str, proc: Any) -> None:
        """Start on a clean page, and refuse to pretend when there is no browser."""
        if await environment_manager.get_info(self.env_name) is None:
            raise RuntimeError(
                f"environment {self.env_name!r} is not registered; "
                f"add it to this config's `env_names`"
            )
        self._failures, self._samples, self._observed = {}, {}, {}
        self._action_failed = False
        await self._close_session()

    async def on_exit(self, status: Any) -> None:
        """Release the page. A continuable identity keeps its memory, not its tab."""
        await self._close_session()
        await super().on_exit(status)

    async def _close_session(self) -> None:
        session = str(getattr(self.ctx, "id", "") or "default")
        try:
            environment = await environment_manager.get(self.env_name)
            if environment is not None:
                await environment.close_session(session)
        except Exception as error:  # noqa: BLE001 - never fail a run over a tab
            logger.warning(f"| ⚠️ [{self.name}] could not close {self.env_name}: {error}")


__all__ = ["BrowserAgent"]
