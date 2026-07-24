"""Canvas module: graph→HTML compilation, extension publishing, drafts, runs."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentevolver.canvas.compiler import (
    CanvasCompileError,
    compile_graph,
    extract_embedded_source,
)
from agentevolver.canvas.server import CanvasManagerServer
from agentevolver.canvas.types import FlowGraph, GraphEdge, GraphNode, Position, workflow_name_for
from agentevolver.workflow import workflow_manager
from agentevolver.workflow.types import StepType


def _node(node_id: str, **kwargs) -> GraphNode:
    return GraphNode(id=node_id, **kwargs)


def _review_graph(name: str = "Canvas review") -> FlowGraph:
    """input(task) → map[agent] → reduce → output, mirroring parallel_review."""
    return FlowGraph(
        name=name,
        nodes=[
            _node("task_in", kind="input", name="task", required=True, description="What to review."),
            _node("angles", kind="input", name="angles", input_type="array", required=True),
            _node("reviews", step_type="map", attrs={"item_name": "angle", "concurrency": 4},
                  position=Position(x=0, y=100)),
            _node("review", step_type="agent", target="general_agent",
                  task="Review from the ${angle} angle: ${inputs.task}", parent="reviews"),
            _node("report", step_type="reduce", target="general_agent",
                  task="Merge the findings into one report.", position=Position(x=0, y=300)),
            _node("out", kind="output", name="report", position=Position(x=0, y=400)),
        ],
        edges=[
            GraphEdge(id="e1", source="angles", target="reviews", param="items"),
            GraphEdge(id="e2", source="reviews", target="report", param="items"),
            GraphEdge(id="e3", source="report", target="out", param="value"),
        ],
    )


def test_compile_produces_valid_registered_workflow_shape() -> None:
    html, definition = compile_graph(_review_graph())
    assert definition.name == "canvas_review"
    assert set(definition.inputs) == {"task", "angles"}
    assert definition.outputs == {"report": "${report}"}
    types = [step.type for step in definition.program]
    assert types == [StepType.MAP, StepType.REDUCE]
    map_step = definition.program[0]
    assert map_step.items == "${inputs.angles}"
    assert map_step.item_name == "angle"
    assert map_step.children[0].type == StepType.AGENT
    reduce_step = definition.program[1]
    assert reduce_step.items == "${reviews}"
    assert definition.enable_evolving is True  # republish must pass the evolvable gate
    assert 'generated-by" content="canvas"' in html


def test_embedded_source_roundtrip() -> None:
    graph = _review_graph(name="Embed me")
    html, _ = compile_graph(graph, embed_source=True)
    recovered = extract_embedded_source(html)
    assert recovered is not None
    assert recovered.name == "Embed me"
    assert {node.id for node in recovered.nodes} == {node.id for node in graph.nodes}
    assert recovered.nodes[2].position == graph.nodes[2].position  # layout survives

    plain, _ = compile_graph(graph)  # without embedding: not canvas-editable
    assert extract_embedded_source(plain) is None


def test_compile_branch_then_else_and_args() -> None:
    graph = FlowGraph(
        name="branchy",
        nodes=[
            _node("check", step_type="tool", target="bash_tool", args={"command": "echo hi"},
                  position=Position(y=0)),
            _node("gate", step_type="branch", attrs={"condition": "${check}"}, position=Position(y=100)),
            _node("yes", step_type="agent", target="general_agent", task="Proceed: ${check}",
                  parent="gate", slot="then"),
            _node("no", step_type="agent", target="general_agent", task="Stop.",
                  parent="gate", slot="else"),
        ],
        edges=[],
    )
    _, definition = compile_graph(graph)
    branch = definition.program[1]
    assert branch.type == StepType.BRANCH and branch.condition == "${check}"
    assert [child.id for child in branch.children] == ["yes"]
    assert [child.id for child in branch.else_children] == ["no"]


def test_compile_orders_steps_by_references() -> None:
    graph = FlowGraph(
        name="ordering",
        nodes=[
            _node("second", step_type="agent", target="general_agent", task="Use ${first}",
                  position=Position(y=0)),
            _node("first", step_type="agent", target="general_agent", task="Start",
                  position=Position(y=500)),
        ],
    )
    _, definition = compile_graph(graph)
    assert [step.id for step in definition.program] == ["first", "second"]


def test_compile_rejects_broken_graphs() -> None:
    with pytest.raises(CanvasCompileError, match="no capability"):
        compile_graph(FlowGraph(name="x", nodes=[_node("a", step_type="tool")]))
    with pytest.raises(CanvasCompileError, match="no steps"):
        compile_graph(FlowGraph(name="x", nodes=[_node("i", kind="input", name="q")]))
    with pytest.raises(CanvasCompileError, match="both connected and set"):
        compile_graph(FlowGraph(
            name="x",
            nodes=[
                _node("a", step_type="tool", target="bash_tool", args={"command": "echo hi"}),
                _node("b", step_type="tool", target="bash_tool", args={"command": "echo bye"}),
            ],
            edges=[GraphEdge(id="e", source="a", target="b", param="arg:command")],
        ))


def test_draft_persistence_is_session_scoped(tmp_path) -> None:
    manager = CanvasManagerServer()
    session_a, session_b = tmp_path / "a" / "canvas", tmp_path / "b" / "canvas"
    draft = FlowGraph(name="wip", nodes=[_node("a", step_type="tool")])  # no target yet: still saves
    saved = manager.save_flow(draft, session_a)
    assert saved.id.startswith("flow-") and not saved.published
    assert manager.get_flow(saved.id, session_a).nodes[0].step_type == "tool"
    # The other session sees no drafts (published flows are global, drafts are not).
    assert all(item["published"] for item in manager.list_flows(session_b))
    assert asyncio.run(manager.delete_flow(saved.id, session_a)) is True


def test_publish_promotes_through_extension_manager(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        from agentevolver.extension import extension_manager
        from agentevolver.version import version_manager

        await version_manager.initialize()
        extension_manager.set_base_dir(str(tmp_path / "extension"))
        manager = CanvasManagerServer()
        flows = tmp_path / "session" / "canvas"
        graph = manager.save_flow(_review_graph(name="publish roundtrip"), flows)
        name = workflow_name_for(graph)
        try:
            published = await manager.publish_flow(graph.id, flows)
            assert published["workflow_name"] == name
            artifact = Path(published["artifact"])
            assert artifact == tmp_path / "extension" / "workflow" / f"{name}.html"
            assert artifact.is_file()
            assert workflow_manager.get(name) is not None
            # The artifact alone is enough to reopen the flow in the canvas.
            assert extract_embedded_source(artifact.read_text(encoding="utf-8")) is not None
            # It shows up as a published (editable) flow in any session.
            listed = manager.list_flows(tmp_path / "elsewhere")
            assert any(item["id"] == f"wf:{name}" for item in listed)
            opened = manager.get_flow(f"wf:{name}", tmp_path / "elsewhere")
            assert opened.published and opened.name == "publish roundtrip"

            # Republishing evolves the version through the extension system.
            first_version = published["version"]
            graph2 = manager.get_flow(graph.id, flows)
            for node in graph2.nodes:
                if node.id == "report":
                    node.task = "Merge and rank the findings."
            manager.save_flow(graph2, flows)
            republished = await manager.publish_flow(graph2.id, flows)
            assert republished["version"] != first_version
        finally:
            await extension_manager.unload("workflow", name)

    asyncio.run(run())


def test_publish_refuses_to_clobber_handwritten_workflow(tmp_path) -> None:
    async def run() -> None:
        manager = CanvasManagerServer()
        flows = tmp_path / "session" / "canvas"
        graph = manager.save_flow(_review_graph(name="parallel review"), flows)
        assert workflow_name_for(graph) == "parallel_review"  # collides with the built-in
        registered_here = workflow_manager.get("parallel_review") is None
        if registered_here:
            workflow_manager.register(
                Path("agentevolver/workflow/default/parallel_review.html"), override=True,
            )
        try:
            with pytest.raises(ValueError, match="hand-written"):
                await manager.publish_flow(graph.id, flows)
        finally:
            if registered_here:
                workflow_manager.unregister("parallel_review")

    asyncio.run(run())


def test_draft_run_executes_on_workflow_runtime(tmp_path) -> None:
    async def run() -> None:
        from agentevolver.tool import tool_manager
        if "bash_tool" not in await tool_manager.list():
            pytest.skip("bash_tool is not registered in this environment")
        manager = CanvasManagerServer()
        graph = FlowGraph(
            name="draft run",
            nodes=[
                _node("say", step_type="tool", target="bash_tool", args={"command": "echo canvas-ok"}),
                _node("out", kind="output", name="said"),
            ],
            edges=[GraphEdge(id="e", source="say", target="out", param="value")],
        )
        run_id = await manager.run_flow(graph)
        for _ in range(100):
            status = manager.run_status(run_id)
            if status["state"] in {"succeeded", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.2)
        assert status["state"] == "succeeded", status.get("error")
        assert "canvas-ok" in str(status["output"])

    asyncio.run(run())
