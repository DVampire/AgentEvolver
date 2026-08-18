"""Inspect — one component's full contract and live registry facts.

One tool for all eight component types, because the ones it replaced differed only
in which manager they asked and which extra lines they printed. Everything
structural — which manager owns a type, whether its members are separately
callable — comes from :data:`COMPONENT_TYPES` rather than from a branch here, so a
new type is a row in that table and a few lines of rendering below.

That table rather than :data:`CAPABILITY_TYPES`: the narrower one is what a model
can *call*, and ``memory`` is not on it. But a memory system is registered,
versioned and evolvable like the rest, so an optimize or evaluate run needs its
source path and its ``enable_evolving`` exactly as much as a tool's — and asking
for it used to answer "unknown capability_type".
"""

import os
from inspect import isawaitable
from typing import Any, Dict, List, Optional, Tuple

from pydantic import Field

from agentevolver.capability import (
    COMPONENT_TYPE_NAMES,
    CapabilityType,
    component_type as component_type_entry,
)
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool
from agentevolver.utils import get_extension_root

_TYPES = ", ".join(COMPONENT_TYPE_NAMES)

_DESCRIPTION = (
    "Fetch one component's full contract (instruction, call schema) plus its live registry "
    f"facts — version, evolvability/enable_evolving, source paths. Types: {_TYPES}."
)

_GUIDANCE = """
Fetch one capability's full contract and live registry facts by type and name.

- The capability rosters in your context are for discovery. Before calling something whose
  arguments or rules you are unsure of, inspect it.
- When optimizing or evaluating a capability: the facts give you its source path (to read
  or edit) and its `enable_evolving` — optimization requires enable_evolving=True, and a
  frozen capability (enable_evolving=False) must NOT be edited.
- Pass the exact name as shown in the roster.
"""

_EXAMPLES = [
    '{{"name": "inspect_tool", "args": {{"capability_type": "tool", "name": "bash_tool"}}}}',
    '{{"name": "inspect_tool", "args": {{"capability_type": "skill", "name": "hello_world_skill"}}}}',
]


def _step_summary(steps) -> List[Dict[str, Any]]:
    """A stable, JSON-friendly view of a workflow's compiled execution tree."""
    return [
        {
            "id": step.id,
            "type": step.type.value,
            "target": step.target,
            "children": _step_summary(step.children),
            "else_children": _step_summary(step.else_children),
        }
        for step in steps
    ]


async def _maybe_await(value: Any) -> Any:
    """Resolve a manager call that may or may not be a coroutine.

    ``workflow_manager`` answers ``get_info`` and ``list`` synchronously while
    every other manager awaits them. Normalising here keeps that difference from
    becoming a branch in the body — it is noted in the module docs as something
    to settle, not something to encode.
    """
    return await value if isawaitable(value) else value


def _extension_path(*parts: str) -> str:
    return os.path.join(get_extension_root(), *parts)


def _fact(label: str, value: Any) -> str:
    return f"- **{label}**: {value}"


def _path_fact(label: str, path: str) -> str:
    """A path line that also says whether anything is there.

    A path alone reads as a promise. Half of what these facts are for is telling
    an optimizer that the file it is about to open does not exist.
    """
    if not path:
        return _fact(label, "(unknown)")
    exists = os.path.exists(path)
    return _fact(label, f"`{path}` (exists: {exists})")


def _sources(capability: str, name: str, info: Any) -> List[str]:
    """Where this capability's source lives, as far as each type can say.

    Deliberately per-type: a tool and a workflow record their own file, a skill
    and a connector their directory, and an agent and an environment record
    nothing — so for those two the extension tree is searched at its conventional
    layout and the answer is honestly "does this path exist".
    """
    if capability == "tool":
        return [_path_fact("Source File", getattr(info, "path", "") or "")]
    if capability == "skill":
        directory = getattr(info, "skill_dir", "") or ""
        return [_path_fact("Skill Directory", directory),
                _path_fact("SKILL.md", os.path.join(directory, "SKILL.md") if directory else "")]
    if capability == "connector":
        directory = getattr(info, "connector_dir", "") or ""
        return [_path_fact("Connector Directory", directory),
                _path_fact("CONNECTOR.md", os.path.join(directory, "CONNECTOR.md") if directory else "")]
    if capability == "agent":
        return [_path_fact("Python File", _extension_path("agent", f"{name}.py")),
                _path_fact("HTML Prompt File", _extension_path("prompt", f"{name}.html"))]
    if capability == "environment":
        directory = _extension_path("environment", name)
        return [_path_fact("Python File (flat)", _extension_path("environment", f"{name}.py")),
                _path_fact("Python File (dir)", os.path.join(directory, "environment.py")),
                _path_fact("ENVIRONMENT.md", os.path.join(directory, "ENVIRONMENT.md"))]
    if capability == "workflow":
        return [_path_fact("Source File", getattr(info, "source_path", "") or "")]
    if capability == "memory":
        return [_path_fact("Python File", getattr(info, "path", "")
                           or _extension_path("memory", f"{name}.py"))]
    return []


def _extras(capability: str, info: Any) -> List[str]:
    """The lines only one type has, and the reason that type's inspect existed."""
    if capability == "connector":
        connection = getattr(info, "connection", {}) or {}
        return [_fact("Transport", connection.get("transport", "(unknown)")),
                _fact("URL/Command", connection.get("url") or connection.get("command") or "(none)")]
    if capability == "agent":
        agent_type = getattr(info, "agent_type", None)
        return [_fact("Agent Type", getattr(agent_type, "value", agent_type) or "tool_calling")]
    if capability == "workflow":
        return [_fact("Schema Version", getattr(info, "schema_version", "")),
                _fact("Program Hash", f"`{getattr(info, 'program_hash', '')}`"),
                _fact("Status", getattr(getattr(info, "status", None), "value", "")),
                _fact("Tags", getattr(info, "tags", None) or "none"),
                _fact("Applicability", getattr(info, "applicability", None) or "not specified"),
                _fact("Inputs", {key: value.model_dump() for key, value in (getattr(info, "inputs", {}) or {}).items()}),
                _fact("Outputs", getattr(info, "outputs", None))]
    return []


def _members(entry: CapabilityType, info: Any) -> List[str]:
    """A container's separately-callable members, by name.

    ``actions`` is a list on a connector and a mapping on an environment; both
    iterate to their names, so neither needs its own branch.
    """
    if not entry.container:
        return []
    return [str(member) for member in (getattr(info, "actions", None) or [])]


@TOOL.register_module(force=True)
class InspectTool(Tool):
    """Return one capability's contract and registry facts, whatever its type."""

    name: str = "inspect_tool"
    # Declared, not inherited: this tool only reads, and `mutates` plus this field
    # are exactly what plan mode looks at.
    permission_mode: str = "read_only"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    mutates: bool = False

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, capability_type: str = "", name: str = "", **kwargs) -> Response:
        """Return one capability's contract and live registry facts.

        Args:
            capability_type: Which kind of component to inspect — one of
                tool, skill, connector, agent, environment, workflow, plugin, memory.
            name: The exact registered name, as shown in the roster.
        """
        entry = component_type_entry(capability_type)
        if entry is None:
            return self._fail(f"Unknown capability_type {capability_type!r}. One of: {_TYPES}.")
        if not name:
            return self._fail(f"'name' is required — the exact registered {entry.type} name.")

        manager = entry.manager()
        info = await _maybe_await(manager.get_info(name))
        if info is None:
            available = await _maybe_await(manager.list())
            return self._fail(f"{entry.type.capitalize()} {name!r} not found. "
                              f"Available {entry.mount_type}: {available}",
                              data={"type": entry.type, "name": name, "registered": False})

        instruction = await self._full_instruction(manager, name)
        lines = [_fact(f"{entry.type.capitalize()} Name", f"`{name}`"),
                 _fact("Registered", True),
                 _fact("Version", getattr(info, "version", "")),
                 _fact("Evolvable (enable_evolving)", bool(getattr(info, "enable_evolving", False)))]
        lines += _extras(entry.type, info)
        members = _members(entry, info)
        if entry.container:
            lines.append(_fact(f"Actions ({len(members)})", ", ".join(members) or "(none listed)"))
        lines += _sources(entry.type, name, info)

        schemas = await self._schemas(manager, name, members)
        for rendered in schemas["markdown"]:
            lines.append(f"\n{rendered}")

        if not instruction:
            # No card to lead with, so the description has to be stated here. When
            # there is one it already opens with the description, and printing it
            # again is the kind of duplication merging six tools into one was for.
            lines.insert(2, _fact("Description", getattr(info, "description", "")))
        body = "\n".join(lines)
        if instruction:
            body = f"{instruction}\n\n## Registry Facts\n{body}"
        detail, detail_data = self._workflow_detail(entry.type, manager, name, info)

        return Response(
            type=ResponseType.TOOL, success=True, message=body + detail,
            data={
                "type": entry.type,
                "name": name,
                "registered": True,
                "enable_evolving": bool(getattr(info, "enable_evolving", False)),
                "members": members,
                "schema": schemas["json"],
                **detail_data,
            },
        )

    # ------------------------------------------------------------------ parts
    @staticmethod
    async def _full_instruction(manager: Any, name: str) -> str:
        """This capability's own instruction, at the level a prompt does not carry.

        The same ``get_instruction`` every roster is built from, asked for one name
        at ``full`` — which is what makes the three levels worth having: the prompt
        pays for guidance, and the parameters and examples it leaves out are exactly
        what an agent comes here for. A manager without one (an agent, a workflow —
        their contract is the schema below) simply contributes nothing.
        """
        render = getattr(manager, "get_instruction", None)
        if render is None:
            return ""
        try:
            return (await _maybe_await(render(allowlist=[name], level="full")) or "").strip()
        except TypeError:  # a manager whose roster predates the level ladder
            return (await _maybe_await(render(allowlist=[name])) or "").strip()

    @staticmethod
    async def _schemas(manager: Any, name: str, members: List[str]) -> Dict[str, Any]:
        """The call schema, per member for a container and once for anything else.

        Both projections come from the manager rather than being rebuilt, so what
        this prints is what the model is actually sent.
        """
        if not members:
            rendered = await manager.get_schema(name, format="md")
            return {"json": await manager.get_schema(name, format="json"),
                    "markdown": [rendered] if rendered else []}
        json_schemas: Dict[str, Any] = {}
        markdown: List[str] = []
        for member in members:
            json_schemas[member] = await manager.get_schema(name, action=member, format="json")
            rendered = await manager.get_schema(name, action=member, format="md")
            if rendered:
                markdown.append(rendered)
        return {"json": json_schemas, "markdown": markdown}

    @staticmethod
    def _workflow_detail(capability: str, manager: Any, name: str,
                         info: Any) -> Tuple[str, Dict[str, Any]]:
        """The compiled tree, the recorded evaluations, and the source, for a workflow.

        The only type whose definition *is* the thing being inspected: an optimizer
        asked to rewrite a workflow needs the HTML and the node tree, not a summary
        of them, and it reads them off ``data`` rather than parsing the message. The
        evaluation record comes from the manager rather than the definition, which is
        why this cannot be one of the plain ``_extras`` lines.
        """
        if capability != "workflow":
            return "", {}
        nodes = _step_summary(getattr(info, "program", []) or [])
        evaluations = [item.model_dump() for item in manager.evaluations(name)]
        summary = manager.evaluation_summary(name)
        source = getattr(info, "source", "") or ""
        text = (f"\n\n- **Evaluation Summary**: {summary}"
                f"\n- **Recorded Evaluations**: {len(evaluations)}"
                f"\n\n## Compiled Nodes\n{nodes}"
                + (f"\n\n## HTML Definition\n```html\n{source.strip()}\n```" if source.strip() else ""))
        return text, {
            "program_hash": getattr(info, "program_hash", ""),
            "status": getattr(getattr(info, "status", None), "value", ""),
            "source_path": getattr(info, "source_path", None),
            "inputs": {key: value.model_dump() for key, value in (getattr(info, "inputs", {}) or {}).items()},
            "outputs": getattr(info, "outputs", None),
            "nodes": nodes,
            "html": source,
            "evaluation_summary": summary,
            "evaluations": evaluations,
        }

    @staticmethod
    def _fail(message: str, data: Optional[Dict[str, Any]] = None) -> Response:
        return Response(type=ResponseType.TOOL, success=False, message=message, data=data)
