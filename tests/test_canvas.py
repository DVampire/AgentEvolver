"""Canvas module: graph→HTML compilation, publishing, drafts, and draft runs."""

from __future__ import annotations

import asyncio

import pytest

from agentevolver.canvas.compiler import CanvasCompileError, compile_graph
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
    assert map_step.children[0].task == "Review from the ${angle} angle: ${inputs.task}"
    reduce_step = definition.program[1]
    assert reduce_step.items == "${reviews}"
    assert 'generated-by" content="canvas"' in html


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
    tool = definition.program[0]
    assert tool.args == {"command": "echo hi"}


def test_compile_orders_steps_by_references() -> None:
    # "second" is placed above "first" visually but references it — reference wins.
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


def test_draft_persistence_allows_incomplete_graphs(tmp_path) -> None:
    async def run() -> None:
        manager = CanvasManagerServer()
        await manager.initialize(flows_dir=str(tmp_path / "flows"),
                                 published_dir=str(tmp_path / "published"),
                                 register_published=False)
        draft = FlowGraph(name="wip", nodes=[_node("a", step_type="tool")])  # no target yet
        saved = manager.save_flow(draft)
        assert saved.id.startswith("flow-") and not saved.published
        loaded = manager.get_flow(saved.id)
        assert loaded.nodes[0].step_type == "tool"
        assert manager.delete_flow(saved.id) is True

    asyncio.run(run())


def test_publish_registers_and_bumps_version_on_change(tmp_path) -> None:
    async def run() -> None:
        manager = CanvasManagerServer()
        await manager.initialize(flows_dir=str(tmp_path / "flows"),
                                 published_dir=str(tmp_path / "published"),
                                 register_published=False)
        graph = manager.save_flow(_review_graph(name="publish roundtrip"))
        name = workflow_name_for(graph)
        try:
            published = await manager.publish_flow(graph.id)
            assert published["workflow_name"] == name
            assert workflow_manager.get(name) is not None
            assert (tmp_path / "published" / f"{name}.html").is_file()

            # Republish with changed content: version must bump automatically.
            graph = manager.get_flow(graph.id)
            for node in graph.nodes:
                if node.id == "report":
                    node.task = "Merge and rank the findings."
            manager.save_flow(graph)
            republished = await manager.publish_flow(graph.id)
            assert republished["version"] == "1.0.1"
            assert workflow_manager.get(name).version == "1.0.1"

            status = manager.flow_status(manager.get_flow(graph.id))
            assert status["registered"] and not status["drifted"]
        finally:
            workflow_manager.unregister(name)

    asyncio.run(run())


def test_restart_reregisters_published_artifacts(tmp_path) -> None:
    async def run() -> None:
        manager = CanvasManagerServer()
        await manager.initialize(flows_dir=str(tmp_path / "flows"),
                                 published_dir=str(tmp_path / "published"),
                                 register_published=False)
        graph = manager.save_flow(_review_graph(name="survives restart"))
        name = workflow_name_for(graph)
        try:
            await manager.publish_flow(graph.id)
            workflow_manager.unregister(name)
            assert workflow_manager.get(name) is None

            fresh = CanvasManagerServer()
            await fresh.initialize(flows_dir=str(tmp_path / "flows"),
                                   published_dir=str(tmp_path / "published"))
            assert workflow_manager.get(name) is not None
        finally:
            workflow_manager.unregister(name)

    asyncio.run(run())


def test_draft_run_executes_on_workflow_runtime(tmp_path) -> None:
    """A tool-only draft runs end-to-end on the real workflow runtime."""
    async def run() -> None:
        from agentevolver.tool import tool_manager
        if "bash_tool" not in await tool_manager.list():
            pytest.skip("bash_tool is not registered in this environment")
        manager = CanvasManagerServer()
        await manager.initialize(flows_dir=str(tmp_path / "flows"),
                                 published_dir=str(tmp_path / "published"),
                                 register_published=False)
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
