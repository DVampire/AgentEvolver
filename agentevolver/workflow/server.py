"""Thin Workflow manager facade plus runtime control."""

from __future__ import annotations

from pathlib import Path
import os
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.utils import assemble_workspace_path

from .compiler import workflow_compiler
from .context import WorkflowContextManager
from .runtime import workflow_runtime
from .types import WorkflowDefinition, WorkflowEvaluation, WorkflowRun, WorkflowStatus


class WorkflowManagerServer(BaseModel):
    """Stable public API delegating registry context and runtime execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None, description="Base directory for Workflow context data")
    workflow_context_manager: Optional[WorkflowContextManager] = Field(default=None, exclude=True)

    def __init__(self, base_dir: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.base_dir = base_dir

    def _ensure_context_manager(self) -> WorkflowContextManager:
        """Lazily create the Context so registry APIs also work before initialize()."""
        if self.workflow_context_manager is None:
            self.workflow_context_manager = WorkflowContextManager(base_dir=self.base_dir)
        return self.workflow_context_manager

    async def initialize(self, workflow_names: Optional[List[str]] = None) -> None:
        self.base_dir = assemble_workspace_path(os.path.join(config.log_root, "workflow"))
        os.makedirs(self.base_dir, exist_ok=True)
        self.workflow_context_manager = WorkflowContextManager(base_dir=self.base_dir)
        await self._ensure_context_manager().initialize(workflow_names=workflow_names)
        logger.info("| ✅ Workflow manager Server initialized")

    async def cleanup(self) -> None:
        await self._ensure_context_manager().cleanup()

    # Registry/context facade — signatures remain compatible with the previous manager.
    def register(self, workflow: WorkflowDefinition | str | Path, *, override=False, status=None):
        return self._ensure_context_manager().register(workflow, override=override, status=status)

    def unregister(self, name: str) -> bool:
        return self._ensure_context_manager().unregister(name)

    def restore(self, name: str, version: str) -> Optional[WorkflowDefinition]:
        return self._ensure_context_manager().restore(name, version)

    def get(self, name: str) -> Optional[WorkflowDefinition]:
        return self._ensure_context_manager().get(name)

    def get_info(self, name: str) -> Optional[WorkflowDefinition]:
        return self._ensure_context_manager().get_info(name)

    def list(self) -> List[str]:
        return self._ensure_context_manager().list()

    def search(self, query: str) -> List[WorkflowDefinition]:
        return self._ensure_context_manager().search(query)

    def get_instruction(self, allowlist=None) -> str:
        return self._ensure_context_manager().get_instruction(allowlist=allowlist)

    async def function_callings(self, allowlist=None) -> List[Tuple[Dict[str, Any], Tuple[Any, ...]]]:
        return await self._ensure_context_manager().function_callings(allowlist=allowlist)

    async def get_schema(self, name: str, action: Optional[str] = None, format: str = "json"):
        return await self._ensure_context_manager().get_schema(name, action=action, format=format)

    def record_evaluation(self, evaluation: WorkflowEvaluation) -> WorkflowEvaluation:
        return self._ensure_context_manager().record_evaluation(evaluation)

    def evaluations(self, name: str) -> List[WorkflowEvaluation]:
        return self._ensure_context_manager().evaluations(name)

    def evaluation_summary(self, name: str) -> Dict[str, Any]:
        return self._ensure_context_manager().evaluation_summary(name)

    # Execution facade — WorkflowRuntime alone owns run state and checkpoints.
    async def run(self, name: str, *, input=None, ctx=None, depth=0) -> WorkflowRun:
        return await workflow_runtime.run(self._require(name), input=input, ctx=ctx, depth=depth)

    def start(self, name: str, *, input=None, ctx=None, depth=0) -> str:
        return workflow_runtime.start(self._require(name), input=input, ctx=ctx, depth=depth)

    def get_run(self, run_id: str) -> Optional[WorkflowRun]:
        return workflow_runtime.get_run(run_id)

    def pause(self, run_id: str) -> bool:
        return workflow_runtime.pause(run_id)

    def continue_run(self, run_id: str) -> bool:
        return workflow_runtime.continue_run(run_id)

    def cancel(self, run_id: str) -> bool:
        return workflow_runtime.cancel(run_id)

    async def run_html(self, source: str, *, input=None, ctx=None) -> WorkflowRun:
        definition = workflow_compiler.compile(source).model_copy(update={"status": WorkflowStatus.EPHEMERAL})
        return await workflow_runtime.run(definition, input=input, ctx=ctx)

    async def resume(self, name: str, checkpoint: str, *, ctx=None, depth=0) -> WorkflowRun:
        return await workflow_runtime.resume(self._require(name), checkpoint, ctx=ctx, depth=depth)

    def _require(self, name: str) -> WorkflowDefinition:
        return self._ensure_context_manager().require(name)


# Backward-compatible class name; new code should prefer WorkflowManagerServer.
WorkflowManager = WorkflowManagerServer
workflow_manager = WorkflowManagerServer()

__all__ = ["WorkflowManager", "WorkflowManagerServer", "workflow_manager"]
