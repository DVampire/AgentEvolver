"""TEMPLATE — a procedural agent (deterministic, code-driven; NO model loop).

Copy to `{extension_root}/agent/{name}.py`, rename the class, and implement the steps.
A procedural agent has NO HTML prompt: it does not reason step by step. Use it when the
task is a fixed pipeline — read → process → report — that you can express in code and
that calls capabilities directly. If the task needs step-by-step reasoning or a dynamic
choice of tool, use `tool_calling_agent_template.py` instead.

There is no separate procedural *type*. The kernel never looks inside `__call__`, so an
agent whose main function is code just overrides that one method, and is dispatched,
suspended, stopped and reaped exactly like any other. Override `__call__` and nothing
else; the runtime phases below are available if the pipeline holds a resource.

Do NOT write an `__init__` that forwards defaults. The base takes `base_dir` and hands
keyword arguments to pydantic; an earlier version of this template defaulted every field
to `None` and passed those through, and pydantic rejects `None` for a `str`, so every
agent generated from it failed to construct.
"""

from typing import Any, Dict, List, Optional

from pydantic import ConfigDict, Field

from agentevolver.agent.types import Agent, AgentContext
from agentevolver.logger import logger
from agentevolver.registry import AGENT
from agentevolver.response.types import Response, ResponseType


@AGENT.register_module(force=True)
class MyProceduralAgent(Agent):
    """A deterministic, code-driven agent with a fixed pipeline."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="my_procedural_agent")
    description: str = Field(default="What this procedure does and when to use it.")
    metadata: Dict[str, Any] = Field(default={})
    enable_evolving: bool = Field(default=True)

    async def __call__(
        self,
        task: str,
        files: Optional[List[str]] = None,
        ctx: AgentContext = None,
        **kwargs: Any,
    ) -> Response:
        """The pipeline. One call in, one Response out — the same contract as the loop."""
        logger.info(f"| 🚀 [{self.name}] {task}")
        try:
            # Reach capabilities through their managers, e.g.
            #   from agentevolver.tool import tool_manager
            #   response = await tool_manager(
            #       name="read_file_tool", input={"path": "..."}, ctx=ctx
            #   )
            data = await self._step_read(task, ctx)
            processed = await self._step_process(data, ctx)
            result = await self._step_report(processed, ctx)
            return Response(
                type=ResponseType.AGENT, success=True,
                message=result, data={"result": result},
            )
        except Exception as error:  # noqa: BLE001 - a pipeline fault is a failed run
            logger.error(f"| ❌ [{self.name}] failed: {error}")
            return Response(type=ResponseType.AGENT, success=False, message=str(error))

    # -- the pipeline's own steps; these are yours, not the base class's -----

    async def _step_read(self, task: str, ctx: AgentContext) -> Any:
        raise NotImplementedError("Implement the read step.")

    async def _step_process(self, data: Any, ctx: AgentContext) -> Any:
        raise NotImplementedError("Implement the process step.")

    async def _step_report(self, processed: Any, ctx: AgentContext) -> str:
        raise NotImplementedError("Implement the report step; return the final string.")


__all__ = ["MyProceduralAgent"]
