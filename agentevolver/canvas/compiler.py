"""Compile a canvas flow graph (JSON) into ``<workflow>`` HTML.

One direction only: the graph is the editable source, the HTML is the build
artifact. The produced document mirrors the shape of the hand-written
workflows under ``agentevolver/workflow/default/`` and is validated with the
real ``WorkflowCompiler`` before it leaves this module, so anything we emit
is guaranteed to register and run.
"""

from __future__ import annotations

import base64
import json
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple
from xml.sax.saxutils import escape, quoteattr

from agentevolver.canvas.types import (
    CALLABLE_STEPS,
    FlowGraph,
    GraphEdge,
    GraphNode,
    STEP_TYPES,
    workflow_name_for,
)
from agentevolver.workflow.compiler import WorkflowCompiler
from agentevolver.workflow.types import WorkflowDefinition


_REF = re.compile(r"\$\{([A-Za-z][A-Za-z0-9_-]*)")

# Graph attrs (python names) -> workflow HTML attribute names.
_ATTR_NAMES = {
    "retries": "retries",
    "retry_delay": "retry-delay",
    "retry_backoff": "retry-backoff",
    "timeout": "timeout",
    "concurrency": "concurrency",
    "max_rounds": "max-rounds",
    "no_progress_limit": "no-progress-limit",
    "min_votes": "min-votes",
}


class CanvasCompileError(ValueError):
    """A graph cannot be compiled; the message lists every problem found."""


# The compiled HTML can carry its own canvas JSON source (base64, so arbitrary
# content can never terminate the HTML comment). This makes a published
# extension artifact self-sufficient: the canvas reopens it for editing
# without any separate JSON store.
_SOURCE_PREFIX = "canvas-flow-source:v2:"
_SOURCE_COMMENT = re.compile(r"<!--\s*" + re.escape(_SOURCE_PREFIX) + r"([A-Za-z0-9+/=\s]+?)-->")


def embed_source_comment(graph: FlowGraph) -> str:
    payload = json.dumps(graph.model_dump(mode="json"), ensure_ascii=False)
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return f"<!-- {_SOURCE_PREFIX}{encoded} -->"


def extract_embedded_source(html: str) -> Optional[FlowGraph]:
    """Recover the canvas JSON source from compiled HTML, or None if absent."""
    match = _SOURCE_COMMENT.search(html or "")
    if match is None:
        return None
    try:
        payload = base64.b64decode(re.sub(r"\s+", "", match.group(1)))
        return FlowGraph.model_validate_json(payload)
    except Exception:  # noqa: BLE001 — a corrupt marker means "not canvas-editable"
        return None


def compile_graph(graph: FlowGraph, embed_source: bool = False) -> Tuple[str, WorkflowDefinition]:
    """Return ``(html, compiled_definition)`` or raise ``CanvasCompileError``."""
    errors: List[str] = []
    nodes = {node.id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        errors.append("Duplicate node ids")

    steps = [node for node in graph.nodes if node.kind == "step"]
    inputs = [node for node in graph.nodes if node.kind == "input"]
    outputs = [node for node in graph.nodes if node.kind == "output"]
    if not steps:
        errors.append("The flow has no steps")
    for node in steps:
        if node.step_type not in STEP_TYPES:
            errors.append(f"Node {node.id} has an unsupported step type: {node.step_type!r}")
        if node.step_type in CALLABLE_STEPS and not node.target:
            errors.append(f"Node {node.id} ({node.step_type}) has no capability selected")
    for node in inputs:
        if not node.name:
            errors.append(f"Input node {node.id} has no name")
    seen_inputs: Set[str] = set()
    for node in inputs:
        if node.name in seen_inputs:
            errors.append(f"Duplicate input name: {node.name}")
        seen_inputs.add(node.name)

    bindings: Dict[Tuple[str, str], str] = {}
    for edge in graph.edges:
        source, target = nodes.get(edge.source), nodes.get(edge.target)
        if source is None or target is None:
            errors.append(f"Edge {edge.id} references a missing node")
            continue
        if source.kind == "output":
            errors.append(f"Edge {edge.id} starts at an output node")
            continue
        key = (edge.target, edge.param)
        if key in bindings:
            errors.append(f"{edge.target}.{edge.param} has more than one incoming edge")
        bindings[key] = _ref(source)
        if edge.param.startswith("arg:"):
            arg_name = edge.param[4:]
            literal = target.args.get(arg_name)
            if isinstance(literal, str) and literal.strip():
                errors.append(
                    f"{edge.target}.{arg_name} is both connected and set to a literal value; clear one"
                )
    if errors:
        raise CanvasCompileError("; ".join(errors))

    ordered = _ordered_children(graph, parent=None, slot="body")
    lines: List[str] = []
    _emit_steps(ordered, graph, bindings, lines, indent=4)

    name = workflow_name_for(graph)
    html = _document(graph, name, inputs, outputs, bindings, lines,
                     source_comment=embed_source_comment(graph) if embed_source else None)
    try:
        definition = WorkflowCompiler().compile(html)
    except Exception as exc:
        raise CanvasCompileError(f"Compiled workflow failed validation: {exc}") from exc
    return html, definition


def _ref(node: GraphNode) -> str:
    return f"${{inputs.{node.name}}}" if node.kind == "input" else f"${{{node.id}}}"


def _ordered_children(graph: FlowGraph, parent: str | None, slot: str) -> List[GraphNode]:
    """Steps under one container slot, topologically ordered by data references
    (bindings + inline ``${id}`` text refs), position as the tiebreaker."""
    children = [
        node for node in graph.nodes
        if node.kind == "step" and node.parent == parent and (parent is None or node.slot == slot)
    ]
    ids = {node.id for node in children}
    incoming: Dict[str, Set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.target in ids and edge.source in ids:
            incoming[edge.target].add(edge.source)
    for node in children:
        for text in (node.task, node.items, *[str(v) for v in node.args.values()]):
            for match in _REF.finditer(text or ""):
                if match.group(1) in ids and match.group(1) != node.id:
                    incoming[node.id].add(match.group(1))

    remaining = {node.id: node for node in children}
    ordered: List[GraphNode] = []
    while remaining:
        ready = [node for node in remaining.values() if not (incoming[node.id] & remaining.keys())]
        if not ready:  # reference cycle — keep a stable order; the runtime will report it
            ready = list(remaining.values())
        ready.sort(key=lambda node: (node.position.y, node.position.x, node.id))
        first = ready[0]
        ordered.append(first)
        del remaining[first.id]
    return ordered


def _emit_steps(
    nodes: List[GraphNode],
    graph: FlowGraph,
    bindings: Dict[Tuple[str, str], str],
    lines: List[str],
    indent: int,
) -> None:
    pad = " " * indent
    for node in nodes:
        tag = node.step_type or "tool"
        attrs: List[str] = [f"id={quoteattr(node.id)}"]
        if node.target:
            attrs.append(f"name={quoteattr(node.target)}")
        task = (node.task or "").strip()
        if task:
            attrs.append(f"task={quoteattr(task)}")
        items = bindings.get((node.id, "items")) or (node.items or "").strip()
        if items:
            attrs.append(f"items={quoteattr(items)}")
        raw_attrs = node.attrs or {}
        if raw_attrs.get("item_name"):
            attrs.append(f"as={quoteattr(str(raw_attrs['item_name']))}")
        condition = str(raw_attrs.get("condition") or "").strip()
        if tag == "branch" and condition:
            attrs.append(f"test={quoteattr(condition)}")
        elif tag == "loop" and condition:
            mode = "while" if raw_attrs.get("condition_mode") == "while" else "until"
            attrs.append(f"{mode}={quoteattr(condition)}")
        for python_name, html_name in _ATTR_NAMES.items():
            value = raw_attrs.get(python_name)
            if value is None or value == "":
                continue
            attrs.append(f"{html_name}={quoteattr(str(value))}")

        args = {
            name: str(value)
            for name, value in (node.args or {}).items()
            if value is not None and str(value).strip() != ""
        }
        for (target_id, param), ref in bindings.items():
            if target_id == node.id and param.startswith("arg:"):
                args[param[4:]] = ref

        # Agent capability mounts → allowlist args (tools/skills/connectors/...).
        # A non-empty selection scopes the agent to exactly those; an empty one
        # is omitted so the agent keeps its defaults. The runtime lifts these
        # args into ctx.extra["<kind>_allowlist"].
        if tag == "agent":
            for kind, names in (node.mounts or {}).items():
                selected = [str(n) for n in names if str(n).strip()]
                if selected:
                    args[kind] = ",".join(dict.fromkeys(selected))

        children_then = _ordered_children(graph, parent=node.id, slot="then" if tag == "branch" else "body")
        children_else = _ordered_children(graph, parent=node.id, slot="else") if tag == "branch" else []
        has_body = bool(args or children_then or children_else)
        opening = f"{pad}<{tag} {' '.join(attrs)}"
        if not has_body:
            lines.append(opening + " />")
            continue
        lines.append(opening + ">")
        for name, value in args.items():
            lines.append(f"{pad}  <arg name={quoteattr(name)} value={quoteattr(value)} />")
        if tag == "branch":
            lines.append(f"{pad}  <then>")
            _emit_steps(children_then, graph, bindings, lines, indent + 4)
            lines.append(f"{pad}  </then>")
            if children_else:
                lines.append(f"{pad}  <else>")
                _emit_steps(children_else, graph, bindings, lines, indent + 4)
                lines.append(f"{pad}  </else>")
        else:
            _emit_steps(children_then, graph, bindings, lines, indent + 2)
        lines.append(f"{pad}</{tag}>")


def _document(
    graph: FlowGraph,
    name: str,
    inputs: List[GraphNode],
    outputs: List[GraphNode],
    bindings: Dict[Tuple[str, str], str],
    flow_lines: List[str],
    source_comment: Optional[str] = None,
) -> str:
    input_lines: List[str] = []
    for node in sorted(inputs, key=lambda item: (item.position.y, item.position.x)):
        attrs = [
            f"name={quoteattr(node.name)}",
            f"type={quoteattr(node.input_type or 'string')}",
            f"required={quoteattr('true' if node.required else 'false')}",
        ]
        if node.default not in (None, ""):
            attrs.append(f"default={quoteattr(str(node.default))}")
        if node.description:
            attrs.append(f"description={quoteattr(node.description)}")
        input_lines.append(f"    <input {' '.join(attrs)} />")

    output_lines: List[str] = []
    for node in sorted(outputs, key=lambda item: (item.position.y, item.position.x)):
        value = bindings.get((node.id, "value")) or (node.value or "").strip()
        if not node.name or not value:
            continue
        output_lines.append(f"    <output name={quoteattr(node.name)} value={quoteattr(value)} />")

    description = graph.description or f"Canvas flow: {graph.name}"
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="UTF-8">',
        f"  <meta name=\"name\" content={quoteattr(name)}>",
        f"  <meta name=\"description\" content={quoteattr(description)}>",
        f"  <meta name=\"version\" content={quoteattr(graph.version)}>",
        '  <meta name="generated-by" content="canvas">',
        f"  <title>{escape(graph.name)} · Canvas Workflow</title>",
        "</head>",
        "<body>",
        "<!-- Generated by the AgentEvolver canvas. Do not edit by hand: open the flow",
        "     in the canvas instead — this file is overwritten on every publish. -->",
        "<workflow",
        f"  name={quoteattr(name)}",
        '  schema-version="1.1.0"',
        f"  version={quoteattr(graph.version)}",
        f"  description={quoteattr(description)}",
        # Canvas flows republish through extension_manager.add_component, whose
        # evolvable gate refuses frozen components — so they must stay evolvable.
        '  enable-evolving="true">',
    ]
    if input_lines:
        parts.append("  <inputs>")
        parts.extend(input_lines)
        parts.append("  </inputs>")
    parts.append("  <flow>")
    parts.extend(flow_lines)
    parts.append("  </flow>")
    if output_lines:
        parts.append("  <outputs>")
        parts.extend(output_lines)
        parts.append("  </outputs>")
    parts.append("</workflow>")
    if source_comment:
        parts.append(source_comment)
    parts.extend(["</body>", "</html>"])
    return "\n".join(parts) + "\n"


__all__ = [
    "CanvasCompileError",
    "compile_graph",
    "embed_source_comment",
    "extract_embedded_source",
]
