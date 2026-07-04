"""Inspect-connector tool — fetch a registered connector's live registry facts on demand."""
import os
from typing import Dict, Any
from pydantic import Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL

_DESCRIPTION = "Fetch a registered connector's live registry facts (registration status, version, evolvability/require_grad, MCP connection, actions, CONNECTOR.md path) by name."

_INSTRUCTION = """
## Function
Fetch a connector's live registry facts: whether it is registered, its version, whether it is evolvable (require_grad), its MCP connection (transport/url), the actions it exposes, and its directory + CONNECTOR.md path.

## Guidance
- Use this before optimizing or evaluating a connector named in your task: it reports the target's real state in the registry, which reading files alone cannot.
- Optimization requires require_grad=True. If inspect_connector reports require_grad=False, the connector is frozen — do NOT edit it; refuse and report why.
- The returned connector_dir / CONNECTOR.md path tells you exactly which files to read/edit.

## Parameters
- name (str): The exact name of the connector to inspect.

## Example
{"name": "inspect_connector", "args": {"name": "pubmed"}}
"""


@TOOL.register_module(force=True)
class InspectConnector(Tool):
    """Return a registered connector's live registry facts on demand."""

    name: str = "inspect_connector"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(self, name: str, **kwargs) -> Response:
        """Return live registry facts for the named connector.

        Args:
            name (str): The exact name of the connector to inspect.
        """
        from src.connector.server import connector_manager  # local import avoids a circular import

        info = await connector_manager.get_info(name)

        lines = [f"- **Connector Name**: `{name}`"]
        if info is None:
            lines.append("- **Registered**: False (not found in registry)")
            available = await connector_manager.list()
            lines.append(f"\nAvailable connectors: {available}")
            return Response(
                type=ResponseType.TOOL, success=False, message="\n".join(lines),
                data={"connector": name, "registered": False, "require_grad": False},
            )

        connector_dir = getattr(info, "connector_dir", "") or ""
        md_path = os.path.join(connector_dir, "CONNECTOR.md") if connector_dir else ""
        connection = getattr(info, "connection", {}) or {}
        actions = getattr(info, "actions", []) or []
        lines.append("- **Registered**: True")
        lines.append(f"- **Description**: {info.description}")
        lines.append(f"- **Version**: {info.version}")
        lines.append(f"- **Evolvable (require_grad)**: {getattr(info, 'require_grad', False)}")
        lines.append(f"- **Transport**: {connection.get('transport', '(unknown)')}")
        lines.append(f"- **URL/Command**: {connection.get('url') or connection.get('command', '(none)')}")
        lines.append(f"- **Actions ({len(actions)})**: {', '.join(actions) if actions else '(none listed)'}")
        lines.append(f"- **Connector Directory**: `{connector_dir}` (exists: {os.path.isdir(connector_dir) if connector_dir else False})")
        lines.append(f"- **CONNECTOR.md**: `{md_path}` (exists: {os.path.exists(md_path) if md_path else False})")

        return Response(
            type=ResponseType.TOOL,
            success=True,
            message="\n".join(lines),
            data={
                "connector": name,
                "registered": True,
                "require_grad": bool(getattr(info, "require_grad", False)),
                "connector_dir": connector_dir,
                "actions": actions,
            },
        )
