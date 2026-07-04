"""Run a test task through a fresh agent, with the target skill available or not (baseline)."""
from typing import Any, Dict
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL
from src.logger import logger
from src.utils import assemble_project_path

_DESCRIPTION = "Run a test task end-to-end through a fresh sub-agent, either WITH the target skill available or as a baseline (no skill), and return the agent's output plus usage — the core of with-skill vs baseline skill evaluation."

_INSTRUCTION = """
## Function
Spawn a fresh sub-agent and have it complete a test task, controlling whether the target skill is available to it. Run it once WITH the skill and once as a BASELINE (no skill), over the same task, to measure whether the skill actually helps — exactly how skill-creator evaluates a skill.

## Guidance
- Use this in the evaluate phase: for each test prompt, call once with mode="with_skill" and once with mode="baseline", then compare the two outputs (and their usage) — did the skill produce a better result?
- The sub-agent is a real reason-act agent; it may take several steps. Keep test tasks focused.
- "with_skill" makes ONLY the target skill visible to the sub-agent (via a per-run skill allowlist); "baseline" makes NO skills visible. This isolates the skill's contribution.
- Returns the sub-agent's final output, success flag, step count, and token usage when available.

## Parameters
- task (str): the test task/prompt for the sub-agent to complete.
- skill_name (str): the target skill to make available in with_skill mode.
- mode (str, optional): "with_skill" (default) or "baseline".
- agent_name (str, optional): which registered agent to run as (default "general_agent").

## Example
{"name": "skill_test_runner", "args": {"task": "Summarize the sales figures in /data/q4.csv", "skill_name": "csv_summary_skill", "mode": "with_skill"}}
"""


@TOOL.register_module(force=True)
class SkillTestRunnerTool(Tool):
    """Run a test task through a fresh sub-agent, with or without the target skill."""

    name: str = "skill_test_runner"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(
        self,
        task: str,
        skill_name: str,
        mode: str = "with_skill",
        agent_name: str = "general_agent",
        **kwargs,
    ) -> Response:
        """Run `task` through a fresh sub-agent, with the target skill available or not.

        Args:
            task (str): the test task.
            skill_name (str): target skill (made available in with_skill mode).
            mode (str): "with_skill" or "baseline".
            agent_name (str): registered agent to run as.
        """
        from src.agent.server import agent_manager
        from src.agent.types import AgentContext
        from src.utils.name_utils import make_id

        if mode not in ("with_skill", "baseline"):
            return Response(type=ResponseType.TOOL, success=False,
                message=f"Invalid mode '{mode}'. Use 'with_skill' or 'baseline'.")

        sub_agent = await agent_manager.get(agent_name)
        if sub_agent is None:
            available = await agent_manager.list()
            return Response(type=ResponseType.TOOL, success=False,
                message=f"Agent '{agent_name}' not registered. Available: {available}")

        # Per-run skill visibility: with_skill → only the target skill; baseline → none.
        allowlist = [] if mode == "baseline" else [skill_name]

        tool_ctx = kwargs.get("ctx")
        work_dir = getattr(tool_ctx, "work_dir", None) or assemble_project_path("work_dir/skill_test")
        ctx = AgentContext(
            id=make_id(),
            work_dir=work_dir,
            extra={"skill_allowlist": allowlist},
        )

        logger.info(f"| 🧪 skill_test_runner: running task in mode='{mode}' (skill={skill_name}, agent={agent_name})")
        try:
            resp = await sub_agent(task=task, ctx=ctx)
        except Exception as e:
            logger.error(f"| ❌ skill_test_runner sub-agent failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Sub-agent run failed: {e}")

        rdata = resp.data if isinstance(getattr(resp, "data", None), dict) else {}
        usage = rdata.get("usage") or rdata.get("step_tokens")
        data = {
            "mode": mode,
            "skill_name": skill_name,
            "agent": agent_name,
            "success": bool(getattr(resp, "success", False)),
            "output": getattr(resp, "message", "") or "",
            "usage": usage,
        }
        msg = f"[{mode}] sub-agent completed (success={data['success']}). Output length: {len(data['output'])} chars."
        return Response(type=ResponseType.TOOL, success=True, message=msg, data=data)
