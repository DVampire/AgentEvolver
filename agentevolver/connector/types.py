"""Connector type definitions for the Connector Context Protocol (MCP servers)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from agentevolver.session import BaseContext
from agentevolver.response.types import Response, ResponseType


class ConnectorContext(BaseContext):
    """Context passed into connector manager and individual connector invocations."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(default="", description="Unique identifier for this connector invocation.")
    name: str = Field(default="", description="Name of the connector (MCP server) being invoked.")
    workspace_root: Optional[str] = Field(default=None, description="Working directory available to the connector.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload passed to the connector.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this connector context.")


class ConnectorConfig(BaseModel):
    """Configuration for a loaded connector, parsed from connector.json and its directory."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(description="Connector (MCP server) name from connector.json")
    description: str = Field(default="", description="Connector description from connector.json")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional connector.json fields")
    enable_evolving: bool = Field(default=False, description="Whether the connector is trainable")
    permission_mode: str = Field(default="workspace_write", description="Permission mode: read_only / workspace_write / danger_full_access")
    version: str = Field(default="1.0.0", description="Version of the connector")
    type: str = Field(default="worker", description="Connector type label from connector.json (free-form parameter, no special handling)")

    connector_dir: str = Field(default="", description="Absolute path to the connector directory")
    content: str = Field(default="", description="Full markdown body of CONNECTOR.md (module intro + per-tool detailed docs, after frontmatter)")
    connection: Dict[str, Any] = Field(default_factory=dict, description="MCP connection config (transport/command/args/url/... — MultiServerMCPClient format)")
    actions: List[str] = Field(default_factory=list, description="Discovered/declared MCP tool (action) names exposed by this server")
    #: Per-action argument schemas, keyed by action name — the ``inputSchema`` an MCP
    #: server declares for each of its tools. Populated from the server the first time
    #: one is reached (see ``ConnectorContextManager.discover`` and ``_invoke_mcp``), and
    #: readable from CONNECTOR.md frontmatter so a run that has not connected yet still
    #: has them. An action with no entry here is sent to the model as a permissive
    #: object, which tells it the action exists and nothing about what to pass.
    action_schemas: Dict[str, Any] = Field(default_factory=dict, description="Per-action argument schemas")

    #: Per-action descriptions from the same source. Without these every action of a
    #: connector is described to the model by the *connector's* one-line description,
    #: which is the same sentence twenty times over.
    action_descriptions: Dict[str, str] = Field(default_factory=dict, description="Per-action descriptions")
    action_annotations: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-action MCP effect annotations (readOnly/destructive/idempotent/openWorld).",
    )

    def model_dump(self, **kwargs) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "metadata": self.metadata,
            "enable_evolving": self.enable_evolving,
            "permission_mode": self.permission_mode,
            "version": self.version,
            "type": self.type,
            "connector_dir": self.connector_dir,
            "content": self.content,
            "connection": self.connection,
            "actions": self.actions,
            "action_schemas": self.action_schemas,
            "action_descriptions": self.action_descriptions,
            "action_annotations": self.action_annotations,
        }


__all__ = [
    "ConnectorConfig",
    "ConnectorContext",
]
