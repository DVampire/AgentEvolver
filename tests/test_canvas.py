"""What someone draws on the canvas is what the workflow runtime runs.

The canvas keeps a JSON graph as its source of truth and compiles it, on demand, to a
transient `<workflow>` document that the real `WorkflowCompiler` validates. That is the
whole safety story: a graph either compiles into a definition the runtime will accept, or
it fails at compile time with every problem listed. Nothing in between should reach a run.

The compiler decisions pinned here are the ones with no visible symptom when they go
wrong. An edge wired from the `message` port must become `${node.message}` and not
`${node}`, or a downstream step is handed the whole `{message, data, files}` record where
it expected text. Branch and map bodies are derived from which control port reaches a
node, so a mistake there silently moves a step into or out of a body rather than raising.
Step order comes from references, not from where boxes sit on the screen — a graph laid
out bottom-to-top must still run in dependency order. And a capability picker left empty
must not compile to an empty allowlist, which would scope an agent to no tools at all
instead of leaving it its defaults.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentevolver.canvas.compiler import CanvasCompileError, canvas_compiler
from agentevolver.canvas.server import CanvasManagerServer
from agentevolver.canvas.types import FlowGraph, GraphEdge, GraphNode, Position, workflow_name_for
from agentevolver.workflow.types import StepType


def _node(node_id: str, **kwargs) -> GraphNode:
    return GraphNode(id=node_id, **kwargs)


def _review_graph(name: str = "Canvas review") -> FlowGraph:
    """input(task) → map[agent] → reduce → output, mirroring parallel_review."""
    return FlowGraph(
        name=name,
        nodes=[
            _node("task_in", type="input", name="task", required=True, description="What to review."),
            _node("angles", type="input", name="angles", input_type="array", required=True),
            _node("reviews", step_type="map", attrs={"item_name": "angle", "concurrency": 4},
                  position=Position(x=0, y=100)),
            _node("review", step_type="agent", target="general_agent",
                  task="Review from the ${angle} angle: ${inputs.task}", parent="reviews"),
            _node("report", step_type="reduce", target="general_agent",
                  task="Merge the findings into one report.", position=Position(x=0, y=300)),
            _node("out", type="output", name="report", position=Position(x=0, y=400)),
        ],
        edges=[
            GraphEdge(id="e1", source="angles", target="reviews", param="items"),
            GraphEdge(id="e2", source="reviews", target="report", param="items"),
            GraphEdge(id="e3", source="report", target="out", param="value"),
        ],
    )


# --------------------------------------------------------------------------- #
# Compiling a graph into a runnable definition
# --------------------------------------------------------------------------- #
def test_a_drawn_graph_compiles_to_the_workflow_it_depicts() -> None:
    """The end-to-end shape: names, inputs, outputs, nesting, and republishability.

    This graph is the canvas version of the hand-written `parallel_review` workflow, so
    it exercises every translation at once — an input node becoming `${inputs.angles}`, a
    map node keeping its `item_name` for the body to read, an agent landing inside the
    map's children rather than beside it, and a reduce consuming `${reviews}`.

    `enable_evolving` is asserted because it is not cosmetic: canvas flows republish
    through `extension_manager.add_component`, whose evolvable gate refuses frozen
    components. Emitting `false` here would let a flow compile and run and then fail only
    at publish, with an error about evolution that names nothing to do with the canvas.
    """
    html, definition = canvas_compiler.compile(_review_graph())
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


def test_branch_bodies_are_derived_from_which_port_reaches_them() -> None:
    """`then` and `else` membership comes from the graph, and there is nothing else to check it.

    The editor draws ordinary nodes wired by ports; the runtime needs nested children. A
    node claimed by the wrong slot compiles cleanly and runs — on the wrong side of the
    condition. That is a flow which does the opposite of what it shows, with no error
    anywhere, so the assignment is pinned by id rather than by count.
    """
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
    _, definition = canvas_compiler.compile(graph)
    branch = definition.program[1]
    assert branch.type == StepType.BRANCH and branch.condition == "${check}"
    assert [child.id for child in branch.children] == ["yes"]
    assert [child.id for child in branch.else_children] == ["no"]


def test_steps_are_ordered_by_what_they_reference_not_by_where_they_sit() -> None:
    """Layout is a human convenience; running in layout order would resolve `${first}` to nothing.

    Here the two nodes are deliberately placed upside down — the step that reads `${first}`
    sits at the top of the canvas and the step producing it at y=500 — and the reference is
    inline in the task text rather than an edge, which is the form a position-based sort
    would miss.
    """
    graph = FlowGraph(
        name="ordering",
        nodes=[
            _node("second", step_type="agent", target="general_agent", task="Use ${first}",
                  position=Position(y=0)),
            _node("first", step_type="agent", target="general_agent", task="Start",
                  position=Position(y=500)),
        ],
    )
    _, definition = canvas_compiler.compile(graph)
    assert [step.id for step in definition.program] == ["first", "second"]


def test_a_graph_that_cannot_run_is_refused_at_compile_time() -> None:
    """Three ways a half-finished canvas would otherwise reach the runtime.

    Drafts are saved incomplete on purpose, so compile is the only gate. A step with no
    capability chosen, and a graph with inputs but nothing to run, would both fail deep
    inside a run instead of before it. The third — an argument that is both wired and typed
    — is the one a user cannot debug alone: the literal is visible in the node while the
    edge silently overwrites it, so the flow reads as doing one thing and does another.
    """
    with pytest.raises(CanvasCompileError, match="no capability"):
        canvas_compiler.compile(FlowGraph(name="x", nodes=[_node("a", step_type="tool")]))
    with pytest.raises(CanvasCompileError, match="no steps"):
        canvas_compiler.compile(FlowGraph(name="x", nodes=[_node("i", type="input", name="q")]))
    with pytest.raises(CanvasCompileError, match="both connected and set"):
        canvas_compiler.compile(FlowGraph(
            name="x",
            nodes=[
                _node("a", step_type="tool", target="bash_tool", args={"command": "echo hi"}),
                _node("b", step_type="tool", target="bash_tool", args={"command": "echo bye"}),
            ],
            edges=[GraphEdge(id="e", source="a", target="b", param="arg:command")],
        ))


# --------------------------------------------------------------------------- #
# Ports: what the editor lets you wire, and what a wire compiles to
# --------------------------------------------------------------------------- #
def test_the_palette_declares_the_port_types_the_editor_wires_on() -> None:
    """Port types are what the editor uses to permit or refuse a connection.

    An untyped port (or one typed `any` by accident) makes every wire legal, and the
    mistake then surfaces as a runtime type error inside a flow the canvas said was
    valid. `map` is the case worth naming: its input is a `list`, and it has two distinct
    outputs — `item` fans out into the body, `done` is the collected result — so collapsing
    them would make a fan-out indistinguishable from its aggregate.
    """
    from agentevolver.canvas.catalog import catalog

    specs = {spec.id: spec for spec in asyncio.run(catalog.build())}
    io_input = specs["io/input"]
    assert [(port.name, port.type) for port in io_input.outputs] == [("out", "any")]
    map_spec = specs["step/map"]
    assert [port.name for port in map_spec.inputs] == ["items"]
    assert map_spec.inputs[0].type == "list"
    # map fans out on ``item`` and collects on ``done``.
    assert [port.name for port in map_spec.outputs] == ["item", "done"]


def test_a_capability_node_offers_its_result_split_as_well_as_whole() -> None:
    """Every capability returns `{message, data, files}`, and the ports mirror that exactly.

    Without the split ports the only way to reach the text of a result would be to wire the
    whole record into a step expecting a string. Each port name is also the sub-path the
    compiler emits, so a renamed or missing port breaks the reference, not just the picker.
    """
    from agentevolver.canvas.catalog import catalog

    agent = next((s for s in asyncio.run(catalog.build()) if s.category == "agent"), None)
    if agent is None:
        pytest.skip("no actor agents registered")
    out = {port.name: port.type for port in agent.outputs}
    assert out == {"message": "text", "data": "object", "files": "list", "out": "any"}


def test_a_wire_from_the_message_port_compiles_to_that_sub_path() -> None:
    """The port is not decoration — it decides what the downstream step receives.

    Dropping `source_port` and always emitting `${ag}` is the easy simplification, and it
    produces a flow that runs: the output is just the whole result record instead of the
    text. Nothing fails; the answer is simply wrong in a way that reads as the agent
    having replied strangely.
    """
    graph = FlowGraph(
        name="typed",
        nodes=[
            _node("ag", step_type="agent", target="general_agent", task="Answer"),
            _node("out", type="output", name="answer"),
        ],
        edges=[GraphEdge(id="e", source="ag", target="out", param="value", source_port="message")],
    )
    _, definition = canvas_compiler.compile(graph)
    # message output port → ${ag.message} sub-path (not the whole ${ag}).
    assert definition.outputs == {"answer": "${ag.message}"}


def test_a_wire_from_the_out_port_compiles_to_the_whole_value() -> None:
    """`out` is the one port with no sub-path, so it must not become `${ag.out}`."""
    graph = FlowGraph(
        name="whole",
        nodes=[
            _node("ag", step_type="agent", target="general_agent", task="Answer"),
            _node("out", type="output", name="answer"),
        ],
        edges=[GraphEdge(id="e", source="ag", target="out", param="value", source_port="out")],
    )
    _, definition = canvas_compiler.compile(graph)
    assert definition.outputs == {"answer": "${ag}"}


# --------------------------------------------------------------------------- #
# Mounting capabilities on an agent node
# --------------------------------------------------------------------------- #
def test_only_a_non_empty_capability_selection_becomes_an_allowlist_arg() -> None:
    """An empty picker means "no opinion", not "nothing allowed".

    The two are one line apart in the compiler and worlds apart at run time: emitting
    `connectors=""` would scope the agent to an empty connector allowlist, and it would
    then fail to do work it was perfectly capable of — while looking configured. Omitting
    the arg leaves the agent its configured defaults.
    """
    graph = FlowGraph(
        name="mounted",
        nodes=[
            _node("q", type="input", name="q", required=True),
            _node("ag", step_type="agent", target="general_agent", task="Answer: ${inputs.q}",
                  mounts={"tools": ["bash_tool", "web_searcher_tool"], "skills": ["debug"], "connectors": []}),
            _node("out", type="output", name="answer"),
        ],
        edges=[GraphEdge(id="e", source="ag", target="out", param="value")],
    )
    _, definition = canvas_compiler.compile(graph)
    agent_step = next(step for step in definition.program if step.type == StepType.AGENT)
    # Non-empty selections become args; the empty one is omitted (agent keeps defaults).
    assert agent_step.args.get("tools") == "bash_tool,web_searcher_tool"
    assert agent_step.args.get("skills") == "debug"
    assert "connectors" not in agent_step.args


def test_lifting_mount_args_derives_a_context_instead_of_editing_the_shared_one() -> None:
    """A map body runs its agents concurrently against one context object.

    The runtime turns the compiled `tools`/`skills` args into `ctx.extra` allowlists. Doing
    that in place is the obvious implementation and is not parallel-safe: siblings under a
    map share the context, so one agent's scoping would silently apply to the others, and
    which one won would depend on scheduling. Popping the args matters for the same reason
    in the other direction — a leftover `tools` key would be forwarded to the agent as if it
    were a task parameter.
    """
    from agentevolver.agent.types import AgentContext
    from agentevolver.workflow.runtime import workflow_runtime

    ctx = AgentContext(extra={"session_id": "s1"})
    payload = {"task": "hi", "tools": "bash_tool,web_searcher_tool", "skills": "debug", "connectors": ""}
    derived = workflow_runtime._apply_agent_mounts(payload, ctx)
    assert payload == {"task": "hi"}  # mount args popped
    assert derived.extra["tool_allowlist"] == ["bash_tool", "web_searcher_tool"]
    assert derived.extra["skill_allowlist"] == ["debug"]
    assert "connector_allowlist" not in derived.extra  # empty selection ignored
    assert "tool_allowlist" not in ctx.extra  # shared ctx untouched (parallel-safe)


# --------------------------------------------------------------------------- #
# Drafts, the reuse library, and running one
# --------------------------------------------------------------------------- #
def test_a_draft_is_saved_where_its_session_says_and_may_be_incomplete(tmp_path) -> None:
    """Saving is not compiling: a flow half-drawn must survive a page reload.

    Refusing to persist a node without a capability chosen — the state every node passes
    through while being wired — would lose work between the moment it is dropped on the
    canvas and the moment it is finished. The draft directory is supplied per call by the
    Gateway, so a second session's listing shows none of this session's drafts.
    """
    manager = CanvasManagerServer()
    session_a, session_b = tmp_path / "a" / "canvas", tmp_path / "b" / "canvas"
    draft = FlowGraph(name="wip", nodes=[_node("a", step_type="tool")])  # no target yet: still saves
    saved = manager.save_flow(draft, session_a)
    assert saved.id.startswith("flow-") and not saved.published
    assert manager.get_flow(saved.id, session_a).nodes[0].step_type == "tool"
    # The other session sees no drafts (published flows are global, drafts are not).
    assert all(item["published"] for item in manager.list_flows(session_b))
    assert asyncio.run(manager.delete_flow(saved.id, session_a)) is True


def test_a_flow_exported_to_the_library_comes_back_as_a_fresh_unbound_draft(tmp_path, monkeypatch) -> None:
    """The library is for reuse, so importing must never alias the copy it came from.

    The failure this rules out is destructive: if the imported graph kept its id, the first
    save after an import would overwrite the original draft, and every session that
    imported the same library flow would be editing one shared document. Layout is checked
    on the way back because a library entry that loses its positions is one nobody reuses
    — the graph is correct and unreadable.
    """
    monkeypatch.setenv("AGENTEVOLVER_EXTENSION_ROOT", str(tmp_path / "extension"))
    manager = CanvasManagerServer()
    flows = tmp_path / "session" / "canvas"
    graph = manager.save_flow(_review_graph(name="library roundtrip"), flows)
    name = workflow_name_for(graph)

    exported = asyncio.run(manager.export_to_library(graph.id, flows))
    assert exported["name"] == name and exported["in_library"]
    assert Path(exported["artifact"]) == tmp_path / "extension" / "canvas" / f"{name}.json"
    assert Path(exported["artifact"]).is_file()
    assert name in manager.list_library_names()
    assert any(item["name"] == "library roundtrip" for item in manager.list_library())

    reopened = manager.import_from_library(name)
    assert reopened.name == "library roundtrip"
    assert {node.id for node in reopened.nodes} == {node.id for node in graph.nodes}
    # Node 2 is the map step, the first node given an explicit position.
    assert reopened.nodes[2].position == graph.nodes[2].position  # layout survives
    # Importing starts a new draft rather than aliasing the library copy.
    assert not reopened.id
    assert manager.save_flow(reopened, flows).id != graph.id

    assert asyncio.run(manager.delete_from_library(name)) is True
    assert name not in manager.list_library_names()


def test_an_unsaved_draft_runs_on_the_shared_workflow_runtime(tmp_path) -> None:
    """The canvas owns no executor: a run is an ephemeral definition on the real runtime.

    Everything above checks the compiled shape; this checks that the shape is actually
    accepted and executed, with the tool's output arriving back through the flow's declared
    output. A canvas that grew its own execution path would drift from the runtime the rest
    of the system uses, and the drift would only be visible once behaviour differed.
    """
    async def run() -> None:
        from agentevolver.tool import tool_manager
        if "bash_tool" not in await tool_manager.list():
            pytest.skip("bash_tool is not registered in this environment")
        manager = CanvasManagerServer()
        graph = FlowGraph(
            name="draft run",
            nodes=[
                _node("say", step_type="tool", target="bash_tool", args={"command": "echo canvas-ok"}),
                _node("out", type="output", name="said"),
            ],
            edges=[GraphEdge(id="e", source="say", target="out", param="value")],
        )
        run_id = await manager.run_flow(graph)
        # Poll for up to 20s (100 × 0.2s) — long enough for a real tool step to finish.
        for _ in range(100):
            status = manager.run_status(run_id)
            if status["state"] in {"succeeded", "failed", "cancelled"}:
                break
            await asyncio.sleep(0.2)
        assert status["state"] == "succeeded", status.get("error")
        assert "canvas-ok" in str(status["output"])

    asyncio.run(run())


# --------------------------------------------------------------------------- #
# Port types, enforced where the binding is made
# --------------------------------------------------------------------------- #
# `PortType` declares that a connection is valid when the two types match or either is
# `any`, and the editor refuses a mismatched drag. That was the whole enforcement, which
# means it was none: a graph reaches the compiler from a file someone edited, from the
# gateway's canvas commands, and from an agent that wrote one. A `files` output wired
# into a `task` input compiled cleanly and handed a step a list of paths where it
# expected a sentence.


def _wired(source_port: str, param: str) -> FlowGraph:
    """Two capability steps and one edge between the named ports."""
    return FlowGraph(
        name="Ports",
        nodes=[
            _node("a", step_type="agent", target="general_agent", task="do a thing"),
            _node("b", step_type="agent", target="general_agent", task="use ${a}"),
        ],
        edges=[GraphEdge(id="e1", source="a", target="b", param=param, source_port=source_port)],
    )


@pytest.mark.parametrize("source_port, param", [
    ("files", "task"),      # list  → text
    ("data", "task"),       # object → text
    ("message", "items"),   # text  → list
    ("data", "items"),      # object → list
])
def test_a_mismatched_edge_is_refused_by_the_compiler(source_port, param):
    with pytest.raises(CanvasCompileError) as refusal:
        canvas_compiler.compile(_wired(source_port, param))
    assert "output" in str(refusal.value) and "input" in str(refusal.value)


@pytest.mark.parametrize("source_port, param", [
    ("message", "task"),    # text → text
    ("files", "items"),     # list → list
    ("out", "task"),        # `out` is the whole value: any, so it fits anything
    ("message", "arg:x"),   # an arg's type lives in the node's params, not in the edge
])
def test_a_connection_the_edge_cannot_disprove_is_allowed(source_port, param):
    """The check only tightens where it is certain.

    `out` and `arg:<param>` cannot be typed from an edge alone — one is the whole node
    value, the other comes from that node's parameter schema — so both read as `any`.
    Rejecting a valid flow is worse than missing an invalid one.
    """
    canvas_compiler.compile(_wired(source_port, param))


def test_the_editor_and_the_compiler_apply_the_same_rule():
    """Two implementations of one sentence, in two languages, that must not drift.

    Small enough that generating one from the other would cost more than it saves, so
    the whole 4×4 truth table is compared instead — the rule has sixteen cases.
    """
    import re

    from agentevolver.canvas.types import ports_compatible

    source = (Path(__file__).resolve().parents[1]
              / "frontend" / "src" / "canvas" / "types.ts").read_text(encoding="utf-8")
    body = re.search(r"export function portsCompatible\([^)]*\)[^{]*\{(.*?)\n\}", source, re.S)
    assert body, "portsCompatible is not where this test expects it"

    def typescript(a: str, b: str) -> bool:
        expression = body.group(1).replace("return", "").strip().rstrip(";")
        return eval(expression.replace("===", "==").replace("||", "or")   # noqa: S307
                    .replace("source", repr(a)).replace("target", repr(b)))

    kinds = ["text", "list", "object", "any"]
    mismatched = [(a, b) for a in kinds for b in kinds
                  if ports_compatible(a, b) != typescript(a, b)]
    assert not mismatched, f"the two implementations disagree on: {mismatched}"


def test_the_catalog_declares_the_types_the_compiler_enforces():
    """A port typed one way in the palette and another in the check would make the
    editor and the compiler disagree about the same flow."""
    from agentevolver.canvas.catalog import _CAPABILITY_OUTPUTS
    from agentevolver.canvas.types import PORT_TYPES

    for port in _CAPABILITY_OUTPUTS:
        if port.name in PORT_TYPES:
            assert port.type == PORT_TYPES[port.name], (
                f"the palette types {port.name} as {port.type}, the compiler as "
                f"{PORT_TYPES[port.name]}"
            )
