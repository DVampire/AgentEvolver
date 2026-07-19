"""Workflow registry context: discovery, contracts, prompt roster, and evidence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.config import config
from agentevolver.logger import logger
from agentevolver.utils import assemble_workspace_path
from agentevolver.version import version_manager
from agentevolver.capability import CapabilitySchema, SchemaSource

from .compiler import workflow_compiler
from .types import WorkflowDefinition, WorkflowEvaluation, WorkflowStatus


class WorkflowContextManager(BaseModel):
    """Own the non-execution lifecycle of registered HTML Workflows.

    This mirrors ToolContextManager and SkillContextManager: the context owns entity
    discovery, registry state, compact prompt context, native schemas, caches, and
    version-scoped evaluation evidence. WorkflowManagerServer remains a thin facade
    and delegates actual execution to WorkflowRuntime.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    base_dir: str = Field(default=None, description="Base directory for Workflow runtime context data")
    builtin_workflows_dir: str = Field(default=None, description="Directory containing built-in Workflow HTML")
    evaluation_path: str = Field(default=None, description="Persisted version-scoped evaluation evidence")

    def __init__(
        self,
        base_dir: Optional[str] = None,
        builtin_workflows_dir: Optional[str | Path] = None,
        evaluation_path: Optional[str | Path] = None,
        # Compatibility with the first Context implementation.
        builtin_dir: Optional[str | Path] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.base_dir = assemble_workspace_path(base_dir or os.path.join(config.log_root, "workflow"))
        os.makedirs(self.base_dir, exist_ok=True)
        selected_builtin_dir = builtin_workflows_dir or builtin_dir or (Path(__file__).parent / "default")
        self.builtin_workflows_dir = str(Path(selected_builtin_dir).resolve())
        self.evaluation_path = str(Path(evaluation_path or os.path.join(self.base_dir, "evaluations.json")))
        self._definitions: Dict[str, WorkflowDefinition] = {}
        self._workflow_history_versions: Dict[str, Dict[str, WorkflowDefinition]] = {}
        self._evaluations: Dict[str, List[WorkflowEvaluation]] = {}
        self._instruction_cache: Dict[Optional[Tuple[str, ...]], str] = {}
        logger.info(f"| 📁 Workflow context manager base directory: {self.base_dir}")

    async def initialize(self, workflow_names: Optional[List[str]] = None) -> None:
        """Load persisted evidence and discover built-in HTML definitions."""
        self._load_evaluations()
        builtin_dir = Path(self.builtin_workflows_dir)
        if not builtin_dir.exists():
            return
        selected = set(workflow_names) if workflow_names is not None else None
        for path in sorted(builtin_dir.glob("*.html")):
            if selected is None or path.stem in selected:
                definition = self.register(path, override=True)
                await version_manager.register_version("workflow", definition.name, definition.version)
        logger.info(f"| ✅ Workflows initialization completed — {len(self._definitions)} workflow(s) loaded")

    def register(
        self,
        workflow: WorkflowDefinition | str | Path,
        *,
        override: bool = False,
        status: Optional[str | WorkflowStatus] = None,
    ) -> WorkflowDefinition:
        """Compile and register one definition, invalidating derived context."""
        if isinstance(workflow, WorkflowDefinition):
            definition = workflow
        elif isinstance(workflow, Path) or (isinstance(workflow, str) and "<workflow" not in workflow):
            definition = workflow_compiler.compile_file(workflow)
        else:
            definition = workflow_compiler.compile(str(workflow))
        if status is not None:
            definition = definition.model_copy(update={"status": WorkflowStatus(status)})
        if definition.name in self._definitions and not override:
            raise ValueError(f"Workflow {definition.name!r} is already registered")
        self._definitions[definition.name] = definition
        self._workflow_history_versions.setdefault(definition.name, {})[definition.version] = definition
        self._invalidate_instruction()
        logger.info(f"| 📝 Registered Workflow '{definition.name}' v{definition.version}")
        return definition

    def unregister(self, name: str) -> bool:
        removed = self._definitions.pop(name, None) is not None
        if removed:
            self._invalidate_instruction()
            logger.info(f"| 🗑️ Unregistered Workflow '{name}'")
        return removed

    def restore(self, name: str, version: str) -> Optional[WorkflowDefinition]:
        """Restore an in-process historical definition and rebuild derived context."""
        definition = self._workflow_history_versions.get(name, {}).get(version)
        if definition is None:
            return None
        self._definitions[name] = definition.model_copy(deep=True)
        self._invalidate_instruction()
        logger.info(f"| 🔄 Restored Workflow '{name}' to v{version}")
        return self._definitions[name]

    def get(self, name: str) -> Optional[WorkflowDefinition]:
        return self._definitions.get(name)

    def get_info(self, name: str) -> Optional[WorkflowDefinition]:
        return self.get(name)

    def list(self) -> List[str]:
        return sorted(self._definitions)

    def search(self, query: str) -> List[WorkflowDefinition]:
        terms = {term.lower() for term in query.split() if term}
        definitions = [
            self._definitions[name] for name in self.list()
            if self._definitions[name].status == WorkflowStatus.ACTIVE
        ]
        if not terms:
            return definitions

        def score(item: WorkflowDefinition) -> int:
            haystack = " ".join([item.name, item.description, item.applicability, *item.tags]).lower()
            return sum(term in haystack for term in terms)

        return sorted((item for item in definitions if score(item)), key=score, reverse=True)

    def get_instruction(self, allowlist: Optional[List[str]] = None) -> str:
        """Render the compact MetaAgent roster; full HTML stays in inspect_workflow."""
        key = None if allowlist is None else tuple(allowlist)
        if key in self._instruction_cache:
            return self._instruction_cache[key]
        names = self.list() if allowlist is None else allowlist
        cards = []
        for name in names:
            item = self.get(name)
            if item is None or item.status != WorkflowStatus.ACTIVE:
                continue
            inputs = ", ".join(item.inputs) or "none"
            tags = ", ".join(item.tags) or "none"
            cards.append(
                f"- **{item.name}** (v{item.version}): {item.description or item.name}\n"
                f"  Inputs: {inputs}; Tags: {tags}"
            )
        text = "\n".join(cards)
        self._instruction_cache[key] = text
        return text

    async def function_callings(self, allowlist=None) -> List[Tuple[Dict[str, Any], Tuple[Any, ...]]]:
        """Project active registrations as native callable schemas."""
        if allowlist == []:
            return []
        names = allowlist if allowlist is not None else self.list()
        output = []
        for name in names:
            definition = self.get(name)
            if definition is None or definition.status != WorkflowStatus.ACTIVE:
                continue
            schema = await self.get_schema(name, format="json")
            if schema:
                output.append((schema, ("workflow", name)))
        return output

    async def get_schema(self, name: str, action: Optional[str] = None, format: str = "json"):
        definition = self.get(name)
        if definition is None or definition.status != WorkflowStatus.ACTIVE:
            return None
        properties, required = {}, []
        for field, spec in definition.inputs.items():
            properties[field] = dict(spec.parameter_schema or {"type": spec.type})
            if spec.required:
                required.append(field)
        parameters: Dict[str, Any] = {
            "type": "object", "properties": properties, "additionalProperties": False,
        }
        if required:
            parameters["required"] = required
        source = (
            SchemaSource.LEGACY_FALLBACK
            if any(spec.schema_source == "legacy_fallback" for spec in definition.inputs.values())
            else SchemaSource.DECLARED
        )
        return CapabilitySchema(
            name=f"workflow__{name}", description=definition.description or name,
            parameters=parameters, strict=source != SchemaSource.LEGACY_FALLBACK, source=source,
        ).render(format)

    def record_evaluation(self, evaluation: WorkflowEvaluation) -> WorkflowEvaluation:
        definition = self.require(evaluation.workflow_name)
        if evaluation.workflow_version != definition.version:
            raise ValueError("Evaluation version does not match the registered workflow")
        self._evaluations.setdefault(definition.name, []).append(evaluation)
        self._save_evaluations()
        return evaluation

    def evaluations(self, name: str) -> List[WorkflowEvaluation]:
        definition = self.get(name)
        if definition is None:
            return []
        return [
            item for item in self._evaluations.get(name, [])
            if item.workflow_version == definition.version
        ]

    def evaluation_summary(self, name: str) -> Dict[str, Any]:
        evaluations = self.evaluations(name)
        successes = [item for item in evaluations if item.success]
        quality = sum(item.quality_score for item in evaluations) / len(evaluations) if evaluations else 0.0
        success_rate = len(successes) / len(evaluations) if evaluations else 0.0
        return {
            "healthy": len(evaluations) >= 3 and success_rate >= 0.8 and quality >= 0.7,
            "runs": len(evaluations),
            "success_rate": success_rate,
            "average_quality": quality,
        }

    def require(self, name: str) -> WorkflowDefinition:
        definition = self.get(name)
        if definition is None:
            raise ValueError(f"Unknown workflow: {name}")
        return definition

    def _invalidate_instruction(self) -> None:
        self._instruction_cache.clear()

    def _load_evaluations(self) -> None:
        path = Path(self.evaluation_path)
        if not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self._evaluations = {
                name: [WorkflowEvaluation.model_validate(item) for item in items]
                for name, items in payload.items()
            }
        except Exception:
            # Corrupt optional evidence must not prevent the runtime from starting.
            self._evaluations = {}

    def _save_evaluations(self) -> None:
        path = Path(self.evaluation_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: [item.model_dump() for item in items] for name, items in self._evaluations.items()}
        fd, temporary = tempfile.mkstemp(prefix=".evaluations-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def cleanup(self) -> None:
        """Release active registry state while retaining persisted evidence on disk."""
        self._definitions.clear()
        self._workflow_history_versions.clear()
        self._evaluations.clear()
        self._invalidate_instruction()
        logger.info("| 🧹 Workflow context manager cleaned up")


__all__ = ["WorkflowContextManager"]
