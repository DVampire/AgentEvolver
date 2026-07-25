"""Type definitions for the plugins module.

A **plugin** is a *packaging* unit: it adapts an external provider (Yahoo, FMP,
Tushare, Akshare, …) into AgentEvolver. It deliberately mirrors Langflow's
``bundles`` and Claude's ``plugin`` concept — the plugin is the container, and
what it *surfaces* is a semantic capability. A plugin's ``kind`` says which
family it surfaces (``data_source`` fetches records; other kinds may come
later). Regardless of kind, every plugin returns the canonical
``{message, data, files}`` :class:`Response` envelope so its output composes
with any other capability on the canvas / in a workflow.

The plugin itself is never a workflow step. A ``data_source`` plugin shows up on
the canvas as a semantic **datasource** node (``StepType.DATASOURCE``); the
runtime dispatches that node to ``plugin_manager``.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field

from agentevolver.session import BaseContext
from agentevolver.response.types import Response


class PluginContext(BaseContext):
    """Context passed into the plugin manager and individual plugin instances."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: str = Field(default="", description="Unique identifier for this plugin call.")
    name: str = Field(default="", description="Name of the plugin being called.")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input payload passed to the plugin.")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary extra data attached to this context.")


class Plugin(BaseModel):
    """Base class for external-provider plugins.

    Subclasses implement :meth:`__call__` (the fetch/action) and return a
    :class:`Response` whose ``data`` carries the payload (for a ``data_source``:
    ``{"records": [...], ...}``), ``message`` a human summary, and ``files`` an
    optional artifact path.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="", description="Registered plugin name (e.g. ``yahoo``).")
    description: str = Field(default="", description="One-line description of the provider.")
    kind: str = Field(default="data_source", description="Plugin family: data_source / software / …")
    instruction: str = Field(default="", description="Full usage instruction, fetched on demand.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary plugin metadata.")
    permission_mode: str = Field(default="read_only", description="Permission mode for this plugin's side effects.")

    async def initialize(self) -> None:
        """Optional async setup (open API clients, read credentials)."""

    async def __call__(self, **kwargs) -> Response:
        """Run the plugin's action and return a canonical Response."""
        raise NotImplementedError("All plugins must implement __call__")

    async def cleanup(self) -> None:
        """Optional teardown of any provider resources."""


__all__ = ["Plugin", "PluginContext"]
