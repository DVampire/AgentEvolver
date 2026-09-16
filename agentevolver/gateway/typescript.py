"""Render the Gateway wire models as TypeScript, so the contract is written once.

The browser and the CLI used to declare these shapes by hand, in another language, in
another build, with nothing able to see both. Two failures follow from that and neither is
theoretical. A field the client declares and the server does not send arrives as
`undefined` while TypeScript stays satisfied, so the break surfaces somewhere else
entirely — a blank panel, or a comparison that quietly takes the wrong branch. A field the
server sends and no client declares is a capability nobody can use until someone notices
it exists; three had been shipping for months when a gate first looked.

A gate over hand-written copies can only ever check the copies it was told about, and the
second mirror went unregistered for exactly as long as it existed. So the copies are gone:
this module walks `model_fields` and emits the declarations, `tests/test_gateway_contract.py`
fails if the checked-in artifact is not what this produces, and adding a field in Python is
the only way to add one in TypeScript.

What is deliberately *not* rendered is any type detail beyond field name, optionality and a
coarse shape. `Dict[str, Any]` against `Record<string, unknown>` is the same claim twice; a
richer mapping would be theatre over payloads whose contents neither side constrains.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path
from types import UnionType
from typing import Any, Dict, List, Literal, Optional, Tuple, Type, Union, get_args, get_origin

from pydantic import BaseModel

from agentevolver.gateway.types import (
    PROTOCOL_VERSION,
    GatewayCommand,
    GatewayError,
    GatewayEvent,
    GatewayResponse,
)

#: Rendered in this order. Declaration order matters in the output only for readability —
#: TypeScript interfaces hoist — but a stable order is what makes the artifact diffable.
MODELS: Tuple[Type[BaseModel], ...] = (
    GatewayCommand,
    GatewayError,
    GatewayResponse,
    GatewayEvent,
)

#: Where the rendered artifact lives, relative to the repository root.
ARTIFACT = Path("frontend/src/protocol/gateway.ts")

_HEADER = """\
// Generated from agentevolver/gateway/types.py — do not edit.
//
// Regenerate with:
//   python -c "from agentevolver.gateway.typescript import write_typescript; write_typescript()"
//
// tests/test_gateway_contract.py fails while this file differs from what the Python
// models render, so an edit here is reverted by the next regeneration rather than kept.
//
// `kind` and `type` on GatewayEvent are two different axes and both are needed: `kind`
// says which envelope shape this is, `type` says which event it carries."""

_SCALARS: Dict[Any, str] = {
    str: "string",
    int: "number",
    float: "number",
    bool: "boolean",
    datetime: "string",
    type(None): "null",
}


def _is_optional(annotation: Any) -> bool:
    """Whether `None` is one of the accepted values.

    Optionality is rendered as `?` on the key rather than `| null` in the type: the server
    omits these fields, and a client that has to handle both an absent key and an explicit
    null has been given two ways to say one thing.
    """
    return get_origin(annotation) in (Union, UnionType) and type(None) in get_args(annotation)


def _render_type(annotation: Any) -> str:
    """One Python annotation as its TypeScript equivalent."""
    origin = get_origin(annotation)

    if origin is Literal:
        # A discriminator. Rendered as the literal so `kind` narrows a union, which is the
        # only reason these fields exist.
        return " | ".join(f"'{value}'" for value in get_args(annotation))

    if origin in (Union, UnionType):
        rendered = [_render_type(arg) for arg in get_args(annotation) if arg is not type(None)]
        return " | ".join(dict.fromkeys(rendered)) or "null"

    if origin in (dict, Dict):
        return "Record<string, unknown>"

    if origin in (list, List, tuple):
        args = get_args(annotation)
        return f"{_render_type(args[0])}[]" if args else "unknown[]"

    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return annotation.__name__

    return _SCALARS.get(annotation, "unknown")


def _render_comment(text: str, indent: str) -> List[str]:
    """A description as wrapped `//` lines, so the reason survives the language change."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > 84 - len(indent):
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return [f"{indent}// {line}" for line in lines]


def _render_model(model: Type[BaseModel]) -> str:
    """One Pydantic model as a TypeScript interface."""
    lines: List[str] = []
    doc = inspect.getdoc(model)
    if doc:
        lines.extend(_render_comment(doc.split("\n\n")[0].replace("\n", " "), ""))
    lines.append(f"export interface {model.__name__} {{")
    for name, field in model.model_fields.items():
        if field.description:
            lines.extend(_render_comment(field.description, "  "))
        optional = "?" if _is_optional(field.annotation) else ""
        lines.append(f"  {name}{optional}: {_render_type(field.annotation)};")
    lines.append("}")
    return "\n".join(lines)


def render_typescript() -> str:
    """The whole artifact: version constant, one interface per model, and the union."""
    blocks = [_HEADER, f"export const PROTOCOL_VERSION = {PROTOCOL_VERSION};"]
    blocks.extend(_render_model(model) for model in MODELS)
    blocks.append(
        "export type GatewayMessage = GatewayEvent | GatewayResponse;\n\n"
        "export function isGatewayEvent(message: GatewayMessage): message is GatewayEvent {\n"
        "  return message.kind === 'event';\n"
        "}"
    )
    return "\n\n".join(blocks) + "\n"


def artifact_path(root: Optional[Path] = None) -> Path:
    """Where the artifact belongs, defaulting to this checkout."""
    return (root or Path(__file__).resolve().parents[2]) / ARTIFACT


def write_typescript(root: Optional[Path] = None) -> bool:
    """Write the artifact. Returns whether it changed, so a caller can report drift."""
    path = artifact_path(root)
    rendered = render_typescript()
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return True


__all__ = [
    "ARTIFACT",
    "MODELS",
    "artifact_path",
    "render_typescript",
    "write_typescript",
]
