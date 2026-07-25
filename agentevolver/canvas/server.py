"""Canvas manager — session-scoped drafts, extension-managed publishing, draft runs.

Storage follows the framework's evolution model:

- **Drafts** are session artifacts: JSON documents in a directory supplied by
  the caller (the Gateway passes ``<session project root>/canvas``).
- **Published flows** are extension components: publishing writes
  ``extension/workflow/<name>.html`` through ``extension_manager`` (versioning,
  archive/rollback, manifest persistence, startup loading, and capability
  change events all come from the extension system). The compiled HTML embeds
  the canvas JSON source, so a published flow can be reopened for editing from
  the artifact alone.
- **Runs** compile in memory and start the workflow runtime as EPHEMERAL
  definitions; the canvas owns no executor.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentevolver.canvas.catalog import catalog
from agentevolver.canvas.compiler import canvas_compiler
from agentevolver.canvas.types import FlowGraph, NodeSpec, workflow_name_for
from agentevolver.logger import logger
from agentevolver.utils import make_id

PUBLISHED_PREFIX = "wf:"


class CanvasManagerServer:
    """Stateless facade: draft directories are supplied per call by the Gateway."""

    async def initialize(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    # ------------------------------------------------------------------
    # Palette
    # ------------------------------------------------------------------

    async def catalog(self) -> List[NodeSpec]:
        return await catalog.build()

    async def mounts(self) -> Dict[str, Any]:
        """Global capability rosters the agent capability picker selects from."""
        return await catalog.build_mounts()

    # ------------------------------------------------------------------
    # Drafts (JSON under the session's output) + published extension flows
    # ------------------------------------------------------------------

    @staticmethod
    def _flow_path(flows_dir: Path, flow_id: str) -> Path:
        safe = "".join(char for char in flow_id if char.isalnum() or char in "-_")
        if not safe or safe != flow_id:
            raise ValueError(f"Invalid flow id: {flow_id!r}")
        return flows_dir / f"{safe}.json"

    def list_flows(self, flows_dir: Path) -> List[Dict[str, Any]]:
        """Current session's drafts plus every published canvas workflow."""
        summaries: List[Dict[str, Any]] = []
        if flows_dir.is_dir():
            for path in sorted(flows_dir.glob("*.json")):
                try:
                    summaries.append(FlowGraph.model_validate_json(path.read_text(encoding="utf-8")).summary())
                except Exception as exc:  # noqa: BLE001 — one bad file must not hide the rest
                    logger.warning(f"| ⚠️ Canvas: unreadable flow file {path.name}: {exc}")
        summaries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)

        from agentevolver.workflow import workflow_manager
        for name in workflow_manager.list():
            definition = workflow_manager.get(name)
            graph = canvas_compiler.extract_source(getattr(definition, "source", "") or "")
            if graph is None:
                continue  # hand-written workflow: not canvas-editable
            summaries.append({
                "id": f"{PUBLISHED_PREFIX}{name}",
                "name": graph.name,
                "description": definition.description,
                "version": definition.version,
                "published": True,
                "updated_at": None,
                "node_count": len([node for node in graph.nodes if node.kind == "step"]),
            })
        return summaries

    def get_flow(self, flow_id: str, flows_dir: Path) -> FlowGraph:
        if flow_id.startswith(PUBLISHED_PREFIX):
            from agentevolver.workflow import workflow_manager
            name = flow_id[len(PUBLISHED_PREFIX):]
            definition = workflow_manager.get(name)
            if definition is None:
                raise ValueError(f"Unknown workflow: {name}")
            graph = canvas_compiler.extract_source(definition.source or "")
            if graph is None:
                raise ValueError(f"Workflow {name} was not created with the canvas and cannot be edited")
            # Editing starts a fresh session draft; publishing evolves the same name.
            graph.id = ""
            graph.version = definition.version
            graph.published = True
            graph.program_hash = definition.program_hash
            return graph
        path = self._flow_path(flows_dir, flow_id)
        if not path.is_file():
            raise ValueError(f"Unknown flow: {flow_id}")
        return FlowGraph.model_validate_json(path.read_text(encoding="utf-8"))

    def save_flow(self, graph: FlowGraph, flows_dir: Path) -> FlowGraph:
        """Persist a session draft. Drafts may be incomplete."""
        node_ids = {node.id for node in graph.nodes}
        if len(node_ids) != len(graph.nodes):
            raise ValueError("Duplicate node ids")
        for edge in graph.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                raise ValueError(f"Edge {edge.id} references a missing node")
        now = datetime.now(timezone.utc).isoformat()
        if not graph.id or graph.id.startswith(PUBLISHED_PREFIX):
            graph.id = f"flow-{make_id()}"
            graph.created_at = now
        graph.created_at = graph.created_at or now
        graph.updated_at = now
        flows_dir.mkdir(parents=True, exist_ok=True)
        path = self._flow_path(flows_dir, graph.id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(graph.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
        return graph

    async def delete_flow(self, flow_id: str, flows_dir: Path) -> bool:
        if flow_id.startswith(PUBLISHED_PREFIX):
            from agentevolver.extension import extension_manager
            name = flow_id[len(PUBLISHED_PREFIX):]
            removed = await extension_manager.unload("workflow", name)
            Path(extension_manager.stage_path("workflow", f"{name}.html")).unlink(missing_ok=True)
            return removed
        path = self._flow_path(flows_dir, flow_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def flow_status(self, graph: FlowGraph) -> Dict[str, Any]:
        """Registry view of a flow: registered version and drift vs last publish."""
        from agentevolver.workflow import workflow_manager
        name = workflow_name_for(graph)
        registered = workflow_manager.get(name)
        return {
            "workflow_name": name,
            "registered": registered is not None,
            "registered_version": getattr(registered, "version", None),
            "drifted": bool(
                graph.published and registered is not None
                and graph.program_hash and registered.program_hash != graph.program_hash
            ),
        }

    # ------------------------------------------------------------------
    # Publish — hand the compiled artifact to the extension system
    # ------------------------------------------------------------------

    async def publish_flow(self, flow_id: str, flows_dir: Path) -> Dict[str, Any]:
        from agentevolver.extension import extension_manager
        from agentevolver.workflow import workflow_manager

        graph = self.get_flow(flow_id, flows_dir)
        html, definition = canvas_compiler.compile(graph, embed_source=True)
        name = definition.name

        existing = workflow_manager.get(name)
        if existing is not None and canvas_compiler.extract_source(existing.source or "") is None:
            raise ValueError(
                f"A hand-written workflow named {name!r} already exists; rename the flow instead of overwriting it"
            )

        artifact = Path(extension_manager.stage_path("workflow", f"{name}.html"))
        temporary = artifact.with_suffix(".html.tmp")
        temporary.write_text(html, encoding="utf-8")
        os.replace(temporary, artifact)
        # The canvas flow was drawn and draft-tested by the user; skip the smoke
        # replay gate that guards unattended evolver writes.
        await extension_manager.add_component("workflow", str(artifact), run_smoke=False)

        registered = workflow_manager.get(name)
        version = getattr(registered, "version", definition.version)
        graph.published = True
        graph.version = version
        graph.program_hash = getattr(registered, "program_hash", definition.program_hash)
        self.save_flow(graph, flows_dir)
        logger.info(f"| 🚀 Canvas published workflow '{name}' v{version} via extension manager")
        return {
            "flow": graph.summary(),
            "workflow_name": name,
            "version": version,
            "program_hash": graph.program_hash,
            "artifact": str(artifact),
        }

    # ------------------------------------------------------------------
    # Draft runs — ephemeral compile, straight into the workflow runtime
    # ------------------------------------------------------------------

    async def run_flow(self, graph: FlowGraph, input: Optional[Dict[str, Any]] = None, ctx: Any = None) -> str:
        from agentevolver.workflow.runtime import workflow_runtime
        from agentevolver.workflow.types import WorkflowStatus

        _, definition = canvas_compiler.compile(graph)
        definition = definition.model_copy(update={"status": WorkflowStatus.EPHEMERAL})
        return workflow_runtime.start(definition, input=input or {}, ctx=ctx)

    def run_status(self, run_id: str) -> Dict[str, Any]:
        from agentevolver.workflow import workflow_manager
        run = workflow_manager.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown canvas run: {run_id}")
        return run.model_dump(mode="json")

    def cancel_run(self, run_id: str) -> bool:
        from agentevolver.workflow import workflow_manager
        return workflow_manager.cancel(run_id)


# Global canvas manager instance
canvas_manager = CanvasManagerServer()

__all__ = ["CanvasManagerServer", "canvas_manager", "PUBLISHED_PREFIX"]
