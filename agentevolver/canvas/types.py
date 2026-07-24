"""Data contracts for the canvas module.

The canvas edits a **flow graph** stored as JSON — the editable source of
truth, holding every node's invocation parameters plus purely visual state.
Publishing compiles the graph to ``<workflow>`` HTML (the build artifact) and
registers it with ``workflow_manager``; running compiles in memory and starts
the workflow runtime directly. The canvas has no executor of its own.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


DOCUMENT_VERSION = 2

# Step ids must satisfy the workflow compiler's id rule.
NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

CALLABLE_STEPS = {"tool", "agent", "skill", "workflow"}
STRUCTURAL_STEPS = {"map", "branch", "loop", "reduce", "verify", "checkpoint"}
STEP_TYPES = CALLABLE_STEPS | STRUCTURAL_STEPS

NodeKind = Literal["step", "input", "output"]
Slot = Literal["body", "then", "else"]
ParamType = Literal["string", "number", "boolean", "select", "json"]


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class ParamSpec(BaseModel):
    """One form field on a palette node (compiled into an ``<arg>``)."""

    name: str
    label: str
    type: ParamType = "string"
    required: bool = False
    default: Any = None
    options: Optional[List[str]] = None
    multiline: bool = False
    description: str = ""
    connectable: bool = True


class NodeSpec(BaseModel):
    """A palette entry the frontend renders and the compiler understands.

    ``id`` is ``<category>/<name>`` (e.g. ``tool/bash_tool``, ``step/map``,
    ``io/input``). Callable specs carry the capability ``target``.
    """

    id: str
    category: Literal["tool", "agent", "workflow", "structural", "io"]
    step_type: Optional[str] = None
    target: Optional[str] = None
    label: str
    description: str = ""
    params: List[ParamSpec] = Field(default_factory=list)
    has_task: bool = False
    has_items: bool = False
    container: bool = False


class GraphNode(BaseModel):
    """One placed node. Fields are a union over the three kinds:

    - ``step``: ``step_type``/``target``/``task``/``args``/``items``/``attrs``
    - ``input``: ``name``/``input_type``/``required``/``default``/``description``
    - ``output``: ``name``/``value``
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: NodeKind = "step"
    step_type: Optional[str] = None
    target: Optional[str] = None
    task: str = ""
    args: Dict[str, Any] = Field(default_factory=dict)
    items: str = ""
    attrs: Dict[str, Any] = Field(default_factory=dict)
    name: str = ""
    input_type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""
    value: str = ""
    parent: Optional[str] = None
    slot: Slot = "body"
    position: Position = Field(default_factory=Position)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not NODE_ID.fullmatch(value):
            raise ValueError(f"Node id must match {NODE_ID.pattern}: {value!r}")
        return value


class GraphEdge(BaseModel):
    """A whole-value binding: the target slot's value becomes ``${source}``.

    ``param`` is ``arg:<name>`` (an argument), ``items`` (map/verify/reduce
    input), or ``value`` (an output node). Inline ``${...}`` references typed
    into task text are part of the text itself, not edges.
    """

    id: str
    source: str
    target: str
    param: str


class FlowGraph(BaseModel):
    """The persisted flow document (one JSON file per flow). Drafts may be
    structurally incomplete; full validation happens at publish/run time."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    name: str = "Untitled flow"
    description: str = ""
    version: str = "1.0.0"
    document_version: int = DOCUMENT_VERSION
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    published: bool = False
    program_hash: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "published": self.published,
            "updated_at": self.updated_at,
            "node_count": len([node for node in self.nodes if node.kind == "step"]),
        }


def workflow_name_for(graph: FlowGraph) -> str:
    """Derive the registry name for a graph: slugified, id-rule compliant."""
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", graph.name.strip()).strip("_") or "canvas_flow"
    if not slug[0].isalpha():
        slug = f"flow_{slug}"
    return slug.lower()


__all__ = [
    "CALLABLE_STEPS",
    "DOCUMENT_VERSION",
    "FlowGraph",
    "GraphEdge",
    "GraphNode",
    "NODE_ID",
    "NodeSpec",
    "ParamSpec",
    "Position",
    "STEP_TYPES",
    "STRUCTURAL_STEPS",
    "workflow_name_for",
]
