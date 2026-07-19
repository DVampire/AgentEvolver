"""Compile a constrained HTML workflow document into safe internal instructions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List

from lxml import html

from .types import StepType, WorkflowDefinition, WorkflowInput, WorkflowStatus, WorkflowStep

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_STEP_TAGS = {item.value for item in StepType}
_CONTAINERS = {"flow", "then", "else"}
_META_TAGS = {"inputs", "input", "outputs", "output", "applicability", "tag", "purpose", "strategy", "evolution"}


class WorkflowCompileError(ValueError):
    pass


class WorkflowCompiler:
    """Parser for Workflow HTML schema version 1.x.

    The compiler deliberately accepts only orchestration tags. It never evaluates
    embedded JavaScript, Python, event handlers, or arbitrary template code.
    """

    SCHEMA_VERSION = "1.0.0"
    def compile_file(self, path: str | Path) -> WorkflowDefinition:
        path = Path(path).resolve()
        definition = self.compile(path.read_text(encoding="utf-8"))
        return definition.model_copy(update={"source_path": str(path)})

    def compile(self, source: str) -> WorkflowDefinition:
        try:
            root = html.fromstring(source)
        except Exception as exc:
            raise WorkflowCompileError(f"Invalid workflow HTML: {exc}") from exc
        workflow = root if root.tag == "workflow" else root.find(".//workflow")
        if workflow is None:
            raise WorkflowCompileError("Document must contain one <workflow> root")
        name = workflow.get("name", "")
        if not _ID.fullmatch(name):
            raise WorkflowCompileError("workflow@name must be a valid identifier")
        flow = workflow.find("flow")
        if flow is None:
            raise WorkflowCompileError("Workflow requires a <flow> section")

        seen: set[str] = set()
        counter = [0]
        program = self._steps(flow, seen, counter)
        if not program:
            raise WorkflowCompileError("Workflow flow cannot be empty")
        inputs = self._inputs(workflow.find("inputs"))
        outputs = {
            item.get("name"): item.get("value")
            for item in workflow.findall("./outputs/output")
            if item.get("name") and item.get("value") is not None
        }
        tags = [" ".join(item.itertext()).strip() for item in workflow.findall("./applicability/tag")]
        applicability_node = workflow.find("applicability")
        applicability = " ".join(applicability_node.itertext()).strip() if applicability_node is not None else ""
        status = WorkflowStatus(workflow.get("status", "active"))
        schema_version = workflow.get("schema-version", self.SCHEMA_VERSION)
        if schema_version.split(".", 1)[0] != self.SCHEMA_VERSION.split(".", 1)[0]:
            raise WorkflowCompileError(
                f"Unsupported workflow schema-version {schema_version}; compiler supports {self.SCHEMA_VERSION}"
            )
        return WorkflowDefinition(
            name=name,
            schema_version=schema_version,
            description=workflow.get("description", ""),
            version=workflow.get("version", "1.0.0"),
            status=status,
            tags=[tag for tag in tags if tag],
            applicability=applicability,
            inputs=inputs,
            program=program,
            outputs=outputs,
            max_agents=self._integer(workflow, "max-agents", 1000, 1, 1000),
            max_concurrency=self._integer(workflow, "max-concurrency", 16, 1, 64),
            max_depth=self._integer(workflow, "max-depth", 8, 1, 32),
            timeout=self._number(workflow.get("timeout")),
            enable_evolving=workflow.get("enable-evolving", "false").lower() == "true",
            source=source,
        )

    def _steps(
        self, parent, seen: set[str], counter: List[int], *, parent_tag: str | None = None,
    ) -> List[WorkflowStep]:
        """Compile direct children without mutating the parsed HTML tree."""
        result = []
        for element in parent:
            if not isinstance(element.tag, str):
                continue
            tag = element.tag.lower()
            if tag not in _STEP_TAGS:
                label = parent_tag or getattr(parent, "tag", "flow")
                raise WorkflowCompileError(f"Unsupported <{tag}> inside <{label}>")
            counter[0] += 1
            step_id = element.get("id") or f"{tag}_{counter[0]}"
            if not _ID.fullmatch(step_id) or step_id in seen:
                raise WorkflowCompileError(f"Invalid or duplicate step id: {step_id!r}")
            seen.add(step_id)
            children, else_children = [], []
            if tag == "branch":
                then = element.find("then")
                otherwise = element.find("else")
                children = self._steps(then, seen, counter) if then is not None else []
                else_children = self._steps(otherwise, seen, counter) if otherwise is not None else []
            else:
                children = self._steps_from_direct_children(element, seen, counter)
            args = {
                arg.get("name"): arg.get("value", "")
                for arg in element.findall("arg") if arg.get("name")
            }
            step = WorkflowStep(
                type=StepType(tag), id=step_id,
                target=element.get("name") or element.get("agent") or element.get("target"),
                task=element.get("task") or self._child_text(element, "task"),
                args=args,
                items=element.get("items"), item_name=element.get("as", "item"),
                condition=element.get("test") or element.get("until") or element.get("while"),
                condition_mode=("while" if element.get("while") is not None else "until"),
                concurrency=self._optional_integer(element, "concurrency", 1, 64),
                max_rounds=self._optional_integer(element, "max-rounds", 1, 100),
                no_progress_limit=self._integer(element, "no-progress-limit", 0, 0, 20),
                retries=self._integer(element, "retries", 0, 0, 10),
                timeout=self._number(element.get("timeout")),
                min_votes=self._integer(element, "min-votes", 1, 1, 100),
                children=children, else_children=else_children,
            )
            self._validate_step(step)
            result.append(step)
        return result

    def _steps_from_direct_children(self, element, seen, counter):
        wrappers = {"arg", "task"}
        candidates = [child for child in element if isinstance(child.tag, str) and child.tag.lower() not in wrappers]
        return self._steps(candidates, seen, counter, parent_tag=element.tag) if candidates else []

    @staticmethod
    def _validate_step(step: WorkflowStep) -> None:
        callable_types = {StepType.AGENT, StepType.TOOL, StepType.SKILL, StepType.CONNECTOR, StepType.WORKFLOW, StepType.REDUCE, StepType.VERIFY}
        if step.type in callable_types and not step.target:
            raise WorkflowCompileError(f"<{step.type.value} id='{step.id}'> requires name/agent")
        if step.type in {StepType.MAP, StepType.VERIFY, StepType.REDUCE} and step.items is None:
            raise WorkflowCompileError(f"<{step.type.value} id='{step.id}'> requires items")
        if step.type == StepType.MAP and not step.children and not step.target:
            raise WorkflowCompileError(f"<map id='{step.id}'> requires a child step or agent")
        if step.type == StepType.LOOP and step.max_rounds is None:
            raise WorkflowCompileError(f"<loop id='{step.id}'> requires bounded max-rounds")
        if step.type == StepType.BRANCH and step.condition is None:
            raise WorkflowCompileError(f"<branch id='{step.id}'> requires test")

    @staticmethod
    def _inputs(node) -> Dict[str, WorkflowInput]:
        if node is None:
            return {}
        result = {}
        for item in node.findall("input"):
            name = item.get("name", "")
            if not _ID.fullmatch(name):
                raise WorkflowCompileError(f"Invalid input name: {name!r}")
            result[name] = WorkflowInput(
                name=name, type=item.get("type", "string"),
                required=item.get("required", "false").lower() in {"true", "required", "1"},
                default=item.get("default"), description=item.get("description", ""),
            )
        return result

    @staticmethod
    def _child_text(element, name: str) -> str:
        child = element.find(name)
        return " ".join(child.itertext()).strip() if child is not None else ""

    @staticmethod
    def _integer(element, name, default, minimum, maximum):
        value = int(element.get(name, default))
        if not minimum <= value <= maximum:
            raise WorkflowCompileError(f"{name} must be between {minimum} and {maximum}")
        return value

    def _optional_integer(self, element, name, minimum, maximum):
        return None if element.get(name) is None else self._integer(element, name, 0, minimum, maximum)

    @staticmethod
    def _number(value):
        if value is None:
            return None
        result = float(value)
        if result <= 0:
            raise WorkflowCompileError("Timeout must be positive")
        return result


workflow_compiler = WorkflowCompiler()

__all__ = ["WorkflowCompileError", "WorkflowCompiler", "workflow_compiler"]
