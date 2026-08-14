"""Declare the agent's tools to it as functions, so it can write a program that calls them.

The model already has each tool's full instruction card in its prompt — arguments,
guidance, examples. What it does not have is any statement that those tools are reachable
from inside a program, or what the call looks like when it is. That is all this renders:
one signature per tool and the rules of the calling convention, deliberately without
repeating the parameter documentation that is already three inches up the same prompt.

A signature and not prose, because the notation is the point. `await tools.read_file_tool(
path=p)` is a line the model has written a million variants of; "call the read file tool
with a path" is one it has to translate first.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from agentevolver.code import RUN_CODE_TOOL

#: Tools a program may never call, whatever the roster says.
#:
#: `run_code_tool` because a program starting a program is a recursion with no purpose
#: that the transport itself cannot bound. `done_tool` because completion is a decision
#: about the whole task: the loop reads it from a dispatched action, and a `done` buried
#: inside a program would be answered by the program, not by the run — the agent would
#: believe it had finished while the loop kept going.
UNCALLABLE = (RUN_CODE_TOOL, "done_tool")

#: JSON Schema type -> what to write in a Python signature. Anything unlisted is `Any`,
#: which is honest: the model reads the tool's own card for what an odd parameter takes.
_TYPES = {
    "string": "str", "integer": "int", "number": "float",
    "boolean": "bool", "array": "list", "object": "dict", "null": "None",
}


def callable_names(names: Sequence[str]) -> List[str]:
    """The subset of ``names`` a program may call, in a stable order."""
    return sorted(name for name in names if name not in UNCALLABLE)


def _annotation(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "Any"
    declared = schema.get("type")
    if isinstance(declared, list):
        parts = [_TYPES.get(entry, "Any") for entry in declared if entry != "null"]
        return parts[0] if len(parts) == 1 else "Any"
    return _TYPES.get(declared, "Any")


def signature(function_calling: Dict[str, Any]) -> str:
    """One tool's declaration, rendered from the same schema the wire call uses.

    Keyword-only, because that is how a binding is invoked and because a positional
    convention would have to fix an argument order the JSON schema does not promise.
    """
    function = (function_calling or {}).get("function") or {}
    name = function.get("name") or "tool"
    parameters = function.get("parameters") or {}
    properties = parameters.get("properties") or {}
    required = [key for key in (parameters.get("required") or []) if key in properties]
    optional = [key for key in properties if key not in required]

    rendered = [f"{key}: {_annotation(properties[key])}" for key in required]
    rendered += [f"{key}: {_annotation(properties[key])} = ..." for key in optional]
    arguments = ", ".join(["*", *rendered]) if rendered else ""
    return f"async def {name}({arguments}) -> str"


def render_sdk(schemas: Sequence[Dict[str, Any]]) -> str:
    """The declarations block: one signature per tool, each with its one-line summary."""
    lines: List[str] = []
    for function_calling in schemas:
        function = (function_calling or {}).get("function") or {}
        description = " ".join((function.get("description") or "").split())
        if description:
            lines.append(f"# {description}")
        lines.append(signature(function_calling))
    return "\n".join(lines)


def code_mode_section(sdk: str) -> str:
    """The prompt section: what a program may call, and the rules it is called under.

    Empty when nothing is callable. An agent holding `run_code_tool` and no other tool
    can still run a program, but telling it about a calling convention with nothing to
    call invites a program written around tools that are not there.
    """
    if not sdk.strip():
        return ""
    return "\n".join([
        "### Calling tools from a program (`run_code_tool`)",
        "",
        "These tools are also callable from inside a `run_code_tool` program, with the "
        "arguments documented above. Three reads is one program instead of three turns, "
        "and only what the program prints or returns comes back — so a search whose "
        "output you only need the count of costs you the count.",
        "",
        "```python",
        sdk,
        "```",
        "",
        "- `await tools.<name>(argument=value)` — keyword arguments only. The result is "
        "the tool's own output as text.",
        "- A call that fails raises `ToolCallError` (with `.tool_name`). Catch it to keep "
        "going; let it propagate to end the program there.",
        "- Independent reads may overlap: `await asyncio.gather(...)`. Keep calls that "
        "change something in order, one at a time — they are dispatched exactly as if you "
        "had emitted them yourself, and each one takes the same permission check, the "
        "same plan-mode gate, and the same approval it would take on its own.",
        "- `print()` what you want to see and `return` the conclusion. Nothing else comes "
        "back, so a program that reads ten files and prints one line costs one line.",
        "- Each program is a fresh process: no variable, import or open file survives to "
        "the next one.",
        f"- Not callable from a program: {', '.join(UNCALLABLE)}. Call those directly.",
    ])


async def sdk_for(names: Sequence[str], manager: Any) -> str:
    """Render the declarations for ``names`` by asking the tool manager for each schema.

    Split from :func:`render_sdk` so the rendering can be tested without a live registry,
    and so the caller decides which roster is visible.
    """
    schemas: List[Dict[str, Any]] = []
    for name in callable_names(names):
        schema: Optional[Dict[str, Any]] = await manager.get_schema(name, format="json")
        if isinstance(schema, dict):
            schemas.append(schema)
    return render_sdk(schemas)


__all__ = [
    "UNCALLABLE",
    "callable_names",
    "code_mode_section",
    "render_sdk",
    "sdk_for",
    "signature",
]
