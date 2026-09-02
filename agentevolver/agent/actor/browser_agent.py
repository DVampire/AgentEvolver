"""BrowserAgent — drives the browser environment via env actions in an observe-act loop."""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.registry import AGENT
from agentevolver.environment.server import environment_manager
from agentevolver.hook.server import hook_manager
from agentevolver.hook.types import HookEvent
from agentevolver.agent.types import (
    Agent,
    AgentContext,
    _TRUNCATED_TURNS_BEFORE_GIVING_UP,
)
from agentevolver.message import ContentPartImage, ContentPartText, ImageURL, Message
from agentevolver.response.types import Response, ResponseType
from agentevolver.utils.name_utils import make_id


@AGENT.register_module(force=True)
class BrowserAgent(Agent):
    """Agent dedicated to the browser environment.

    Each step it observes the environment state (text + screenshots), plans a
    small batch of env actions, and executes them via the environment manager.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="browser_agent")
    description: str = Field(
        default="A browser agent that navigates and operates web pages through the "
                "browser environment: clicking, typing, scrolling, and running "
                "Playwright commands as a fallback."
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
        env_name: str = "browser_environment",
        max_actions: int = 3,
        max_step: int = 30,
        max_screenshots: int = 2,
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
            prompt_name=prompt_name or "browser_agent",
            memory_name=memory_name,
            max_actions=max_actions,
            max_step=max_step,
            review_steps=review_steps,
            enable_evolving=enable_evolving,
            **kwargs,
        )
        self.env_name = env_name
        # Only the latest screenshots are attached to the prompt to bound tokens
        self.max_screenshots = max_screenshots

    # ------------------------------------------------------------------
    # Environment access
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Context builders
    # ------------------------------------------------------------------

    def _required_capability_allowlists(self) -> Dict[str, List[str]]:
        """Keep the generic browser worker on the same minimal contract as user agents."""
        return {
            "tool_allowlist": ["done_tool"],
            "skill_allowlist": [],
            "connector_allowlist": [],
            "plugin_allowlist": [],
            "environment_allowlist": [self.env_name],
            "workflow_allowlist": [],
        }

    async def _get_agent_context(
        self,
        task: str,
        step_number: int = 0,
        ctx: Optional[AgentContext] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Extend the base prompt context with browser-specific fields.

        Adds the observed ``environment_state``, a ``workspace`` snapshot, any
        ``errors`` from the previous step's actions, and the rendered environment
        actions, so the template can ground the next batch of env actions.
        """
        base = await super()._get_agent_context(task, step_number=step_number, ctx=ctx, **kwargs)

        # `environment_state` is filled by `_get_environment_context` below, not here: the
        # base class fills that slot after this method returns, so a value set here would
        # be overwritten — and re-fetched, which for a browser means a second screenshot of
        # the page this step already observed.
        self._observed_state = kwargs.get("browser_state")
        base["workspace"] = self._workspace_snapshot(ctx)

        action_errors = kwargs.get("action_errors") or []
        base["errors"] = "\n".join(f"- {e}" for e in action_errors) if action_errors else ""

        return base

    async def _get_environment_context(self, ctx: AgentContext) -> Dict[str, Any]:
        """The page this step already observed, rather than a fresh read of it.

        This agent runs an observe-act loop: it fetches the state at the top of each step
        and plans against it. The base class would fetch it again here, one screenshot
        later and describing a page the plan was not made against — so the observation is
        reused and only the roster comes from the base.
        """
        slots = await super()._get_environment_context(ctx)
        observed = getattr(self, "_observed_state", None)
        slots["environment_state"] = (observed.get("state") if observed
                                      else "[Environment state unavailable.]")
        return slots

    async def _get_tool_context(self, ctx: AgentContext, **kwargs) -> Dict[str, Any]:
        """Omit rendered tool prose; ``done_tool`` is supplied provider-natively."""
        return {"tool_context": ""}

    async def _capability_skill_slots(self, ctx: AgentContext) -> Dict[str, Any]:
        """No skills: this agent drives a browser and has no use for one.

        The empty slot is what the shared capability module tests, so the block is
        omitted rather than rendered with a notice inside it.
        """
        return {"available_skills": ""}

    async def _get_messages(
        self,
        task: str,
        ctx: AgentContext,
        **kwargs,
    ) -> List[Message]:
        """Build the prompt messages and attach the most recent screenshots.

        Beyond the base text messages, appends up to ``max_screenshots`` deduplicated
        images (previous annotated action + current state) as image content parts on
        the final user message, so vision models can ground their actions.
        """
        messages = await super()._get_messages(task, ctx=ctx, **kwargs)

        # Attach the latest screenshots (previous annotated action + current state)
        # to the user message so vision models can ground their actions.
        browser_state = kwargs.get("browser_state") or {}
        screenshots = (browser_state.get("extra") or {}).get("screenshots") or []

        unique = []
        seen_paths = set()
        for shot in screenshots:
            path = getattr(shot, "screenshot_path", None)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            unique.append(shot)

        user_message = messages[-1]
        if unique and isinstance(user_message.content, list):
            for shot in unique[-self.max_screenshots:]:
                b64 = getattr(shot, "screenshot", None)
                if not b64:
                    continue
                description = getattr(shot, "screenshot_description", "") or "Screenshot"
                user_message.content.append(ContentPartText(text=f"\n[{description}]"))
                user_message.content.append(
                    ContentPartImage(
                        image_url=ImageURL(url=f"data:image/png;base64,{b64}", media_type="image/png")
                    )
                )
        return messages

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def _reset_turn_budget(self, ctx: Any) -> None:
        """Reset per-task limits while a continuable browser identity stays alive."""
        context_id = str(getattr(ctx, "id", "") or "")
        if context_id:
            for constraint in self.constraints:
                constraint._cleanup(context_id)
        self._pending_step_tokens.clear()

    async def on_start(self, task, files, ctx, ref, **kwargs) -> Response:
        """BrowserAgent keeps its own bespoke loop (browser-action turns), so it runs
        via ``__call__`` rather than the base event-driven round loop. Returning the
        Response here lets the runtime resolve the caller synchronously."""
        # `env_name` and the config's `env_names` name the same environment in two
        # places, and nothing made them agree. A mismatch — a typo, or a roster that
        # dropped this environment — degraded silently: state came back empty, the
        # prompt block was absent, and the agent behaved like a browser agent with no
        # browser rather than reporting that it had none.
        if await environment_manager.get_info(self.env_name) is None:
            raise RuntimeError(
                f"environment {self.env_name!r} is not registered; "
                f"add it to this config's `env_names`"
            )
        environment = await environment_manager.get(self.env_name)
        if environment is not None:
            await environment.close_session(str(getattr(ctx, "id", "") or "default"))
        self._observed_state = None
        try:
            return await self.__call__(task=task, files=files, ctx=ctx, **kwargs)
        finally:
            self._reset_turn_budget(ctx)
            if environment is not None:
                await environment.close_session(
                    str(getattr(ctx, "id", "") or "default")
                )

    async def __call__(
        self,
        task: str,
        files: Optional[List[str]] = None,
        **kwargs,
    ) -> Response:
        logger.info(f"| 🚀 Starting BrowserAgent: {task}")

        ctx = kwargs.get("ctx", None)
        if ctx is None:
            ctx = AgentContext()

        if not config.workspace_root:
            config.workspace_root = self.base_dir

        if files:
            logger.info(f"| 📂 Attached files: {files}")
        enhanced_task = task

        task_id = make_id()
        logger.info(f"| 📝 Context ID: {ctx.id}, Task ID: {task_id}")

        parent_session_id = ctx.parent_session_id if ctx else None
        subtask_id = ctx.subtask_id if ctx else None

        # ON_START
        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.ON_START, "agent_name": self.name, "task_id": task_id, "task": enhanced_task, "memory_name": self.memory_name, "use_memory": self.use_memory, "parent_session_id": parent_session_id, "subtask_id": subtask_id},
            ctx=ctx,
        )

        step_number = 0
        action_errors: list = []
        truncated_turns = 0
        response = {"done": False, "result": None, "reasoning": None, "action_errors": []}

        while step_number < self.max_step:
            logger.info(f"| 🔄 Step {step_number+1}/{self.max_step}")
            reason, constraint_status = await self._constraint_check(task_id, ctx)
            if reason is not None:
                logger.warning(f"| 🛑 {self.name} constraint violated: {reason}")
                response = {"done": True, "result": reason, "reasoning": None,
                            "action_errors": [], "stopped_by_constraint": True}
                break
            # Observe the fresh page before reasoning over it
            browser_state = await environment_manager.get_state(self.env_name, ctx=ctx)
            messages = await self._get_messages(
                enhanced_task,
                ctx=ctx,
                files=files,
                browser_state=browser_state,
                step_number=step_number,
                action_errors=action_errors,
                constraint_status=constraint_status,
            )
            response = await self._think_and_act(messages, task_id, step_number, ctx=ctx)
            step_number += 1
            action_errors = response.get("action_errors") or []
            if response.get("truncated"):
                truncated_turns += 1
                if truncated_turns >= _TRUNCATED_TURNS_BEFORE_GIVING_UP:
                    response = {
                        "done": False,
                        "result": (
                            f"The model hit its output-token limit {truncated_turns} "
                            "times before completing an action."
                        ),
                        "reasoning": "Repeated incomplete max_tokens responses.",
                        "action_errors": action_errors,
                    }
                    break
            else:
                truncated_turns = 0
            if response["done"]:
                break

        if step_number >= self.max_step and not response["done"]:
            logger.warning(f"| 🛑 Reached max steps ({self.max_step}), stopping...")
            response = {
                "done": False,
                "result": "The task has not been completed.",
                "reasoning": "Reached the maximum number of steps.",
            }

        success = response["done"] and not response.get("stopped_by_constraint", False)

        # ON_STOP
        await hook_manager(
            name="trace_hook",
            input={"event": HookEvent.ON_STOP, "agent_name": self.name, "task_id": task_id, "result": response.get("result"), "success": success, "memory_name": self.memory_name, "use_memory": self.use_memory, "parent_session_id": parent_session_id, "subtask_id": subtask_id},
            ctx=ctx,
        )

        logger.info(f"| ✅ BrowserAgent completed after {step_number}/{self.max_step} steps")

        return Response(type=ResponseType.AGENT,
            success=success,
            message=response["result"],
            data=response,
        )
