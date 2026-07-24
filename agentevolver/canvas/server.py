"""Canvas manager — JSON flow persistence, publish-to-workflow, draft runs.

The canvas owns no executor: publishing registers the compiled HTML with
``workflow_manager``; running starts the workflow runtime on an ephemeral
compiled definition, so drafts run without touching the registry.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentevolver.canvas.catalog import build_catalog
from agentevolver.canvas.compiler import compile_graph
from agentevolver.canvas.types import FlowGraph, NodeSpec, workflow_name_for
from agentevolver.logger import logger
from agentevolver.utils import make_id


class CanvasManagerServer:
    """Owns flow JSON documents and their published HTML artifacts."""

    def __init__(self) -> None:
        self._flows_dir: Optional[Path] = None
        self._published_dir: Optional[Path] = None

    async def initialize(
        self,
        flows_dir: Optional[str] = None,
        published_dir: Optional[str] = None,
        register_published: bool = True,
    ) -> None:
        from agentevolver.utils.path_utils import home_dir
        base = Path(home_dir()) / "canvas"
        self._flows_dir = Path(flows_dir) if flows_dir else base / "flows"
        self._published_dir = Path(published_dir) if published_dir else base / "workflows"
        self._flows_dir.mkdir(parents=True, exist_ok=True)
        self._published_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"| 📁 Canvas manager flows directory: {self._flows_dir}")
        if register_published:
            self._register_published()

    def _register_published(self) -> None:
        """Re-register previously published canvas workflows after a restart."""
        from agentevolver.workflow import workflow_manager
        for path in sorted(self._published_dir.glob("*.html")):
            try:
                workflow_manager.register(path, override=True)
            except Exception as exc:  # noqa: BLE001 — one bad artifact must not block startup
                logger.warning(f"| ⚠️ Canvas: could not re-register {path.name}: {exc}")

    async def cleanup(self) -> None:
        return None

    # ------------------------------------------------------------------
    # Palette
    # ------------------------------------------------------------------

    async def catalog(self) -> List[NodeSpec]:
        return await build_catalog()

    # ------------------------------------------------------------------
    # Flow persistence (JSON drafts — the editable source of truth)
    # ------------------------------------------------------------------

    def _require_dir(self) -> Path:
        if self._flows_dir is None:
            raise RuntimeError("Canvas manager is not initialized")
        return self._flows_dir

    def _flow_path(self, flow_id: str) -> Path:
        safe = "".join(char for char in flow_id if char.isalnum() or char in "-_")
        if not safe or safe != flow_id:
            raise ValueError(f"Invalid flow id: {flow_id!r}")
        return self._require_dir() / f"{safe}.json"

    def list_flows(self) -> List[Dict[str, Any]]:
        summaries = []
        for path in sorted(self._require_dir().glob("*.json")):
            try:
                summaries.append(FlowGraph.model_validate_json(path.read_text(encoding="utf-8")).summary())
            except Exception as exc:  # noqa: BLE001 — one bad file must not hide the rest
                logger.warning(f"| ⚠️ Canvas: unreadable flow file {path.name}: {exc}")
        summaries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return summaries

    def get_flow(self, flow_id: str) -> FlowGraph:
        path = self._flow_path(flow_id)
        if not path.is_file():
            raise ValueError(f"Unknown flow: {flow_id}")
        return FlowGraph.model_validate_json(path.read_text(encoding="utf-8"))

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

    def save_flow(self, graph: FlowGraph) -> FlowGraph:
        """Persist a draft. Drafts may be incomplete; only ids/edges are checked."""
        node_ids = {node.id for node in graph.nodes}
        if len(node_ids) != len(graph.nodes):
            raise ValueError("Duplicate node ids")
        for edge in graph.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                raise ValueError(f"Edge {edge.id} references a missing node")
        now = datetime.now(timezone.utc).isoformat()
        if not graph.id:
            graph.id = f"flow-{make_id()}"
            graph.created_at = now
        graph.created_at = graph.created_at or now
        graph.updated_at = now
        path = self._flow_path(graph.id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(graph.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
        return graph

    def delete_flow(self, flow_id: str) -> bool:
        path = self._flow_path(flow_id)
        if not path.is_file():
            return False
        try:
            graph = FlowGraph.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            graph = None
        path.unlink()
        if graph is not None and graph.published:
            from agentevolver.workflow import workflow_manager
            name = workflow_name_for(graph)
            try:
                workflow_manager.unregister(name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"| ⚠️ Canvas: could not unregister workflow {name}: {exc}")
            if self._published_dir is not None:
                (self._published_dir / f"{name}.html").unlink(missing_ok=True)
        return True

    # ------------------------------------------------------------------
    # Publish — compile the JSON source into the registered HTML artifact
    # ------------------------------------------------------------------

    async def publish_flow(self, flow_id: str) -> Dict[str, Any]:
        from agentevolver.workflow import workflow_manager

        graph = self.get_flow(flow_id)
        html, definition = compile_graph(graph)
        name = definition.name
        registered = workflow_manager.get(name)
        if (
            registered is not None
            and registered.version == definition.version
            and registered.program_hash != definition.program_hash
        ):
            # Content changed at the same version — bump the patch and recompile.
            major, minor, patch = (graph.version.split(".") + ["0", "0"])[:3]
            graph.version = f"{major}.{minor}.{int(patch) + 1}"
            html, definition = compile_graph(graph)
        definition = workflow_manager.register(html, override=True)

        artifact = self._published_dir / f"{name}.html"
        temporary = artifact.with_suffix(".html.tmp")
        temporary.write_text(html, encoding="utf-8")
        os.replace(temporary, artifact)

        graph.published = True
        graph.program_hash = definition.program_hash
        self.save_flow(graph)
        logger.info(f"| 🚀 Canvas published workflow '{name}' v{definition.version}")
        return {
            "flow": graph.summary(),
            "workflow_name": name,
            "version": definition.version,
            "program_hash": definition.program_hash,
            "artifact": str(artifact),
        }

    # ------------------------------------------------------------------
    # Draft runs — ephemeral compile, straight into the workflow runtime
    # ------------------------------------------------------------------

    async def run_flow(self, graph: FlowGraph, input: Optional[Dict[str, Any]] = None, ctx: Any = None) -> str:
        from agentevolver.workflow.runtime import workflow_runtime
        from agentevolver.workflow.types import WorkflowStatus

        _, definition = compile_graph(graph)
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

__all__ = ["CanvasManagerServer", "canvas_manager"]
