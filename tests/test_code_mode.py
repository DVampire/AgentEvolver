"""A program's tool calls are dispatched by the agent, not by the program's own tool.

Code mode lets the model write a program that calls its tools, which turns three turns
into one. The whole risk of that trade is in one place: the bindings. A binding that
reached `tool_manager` directly would work perfectly and would be a second, unchecked way
to call every tool the agent has — no permission check, no plan-mode gate, no hooks, no
trace. Nothing would report it, because the tools would still do their jobs; the only
visible symptom would be a run that edited files while it was supposed to be planning.

So the tests that matter here are the ones that would still pass if the bindings went
around the guard, and only fail because they do not: a real refusal by a real tool
reaching the program, and plan mode stopping a call made from inside one. The rest pin
the substrate (a program is a separate process, its output survives its own timeout) and
the declarations other machinery reads — `mutates`, the transport's name, the budgets.
"""

import asyncio
import inspect
import os
import time
from pathlib import Path
from typing import Any, Dict

import pytest
import pytest_asyncio

from agentevolver.agent.types import Agent, AgentContext
from agentevolver.code import RUN_CODE_TOOL, CodeFailureKind, code_runtime
from agentevolver.config import config
from agentevolver.hook.context import HookContextManager
from agentevolver.hook.server import hook_manager
from agentevolver.plan.server import PlanManagerServer, action_is_allowed, declaration_of
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.default.code_mode import RunCodeTool
from agentevolver.tool.default.code_mode.sdk import (
    UNCALLABLE,
    callable_names,
    code_mode_section,
    render_sdk,
    signature,
)
from agentevolver.tool.default.write_file import WriteFileTool
from agentevolver.tool.server import tool_manager
from agentevolver.tool.types import Tool


class ReadOnlyProbe(Tool):
    """A tool that declares it changes nothing, so the plan gate lets it through."""

    name: str = "read_only_probe_tool"
    description: str = "Echo a value back."
    mutates: bool = False
    permission_mode: str = "read_only"

    async def __call__(self, value: str = "", **kwargs) -> Response:
        return Response(type=ResponseType.TOOL, success=True, message=f"probe saw {value}")


@pytest_asyncio.fixture
async def bench(monkeypatch, tmp_path):
    """An agent, a workspace, and the three tools these tests dispatch.

    Registered against the real `tool_manager` rather than a stub, because the claim
    under test is about the route a call takes through the real managers — a fake
    registry would let the bindings skip everything and still go green.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(config, "workspace_root", str(workspace), raising=False)

    for tool in (ReadOnlyProbe, WriteFileTool, RunCodeTool):
        await tool_manager.register(tool, config={})
        # `register()` builds its ToolConfig without the class's own `mutates` — only the
        # startup registry load reads that field — so every tool registered here would
        # reach the plan gate as undeclared, and the gate would refuse the read-only one
        # too. Copied across rather than worked around: what is being tested is the gate
        # reading a declaration, so the declaration has to be there.
        info = await tool_manager.get_info(tool.model_fields["name"].default)
        info.mutates = tool.model_fields["mutates"].default

    class Probe(Agent):
        name: str = "code_mode_probe_agent"
        description: str = "Dispatches the programs these tests write."
        metadata: Dict[str, Any] = {}

    agent = Probe(base_dir=str(workspace))
    routing = {
        "read_only_probe_tool": ("tool", "read_only_probe_tool"),
        "write_file_tool": ("tool", "write_file_tool"),
        RUN_CODE_TOOL: ("tool", RUN_CODE_TOOL),
        "done_tool": ("tool", "done_tool"),
        "some_skill": ("skill", "some_skill"),
    }
    ctx = AgentContext(id="code-mode-session")
    yield {
        "agent": agent,
        "ctx": ctx,
        "routing": routing,
        "workspace": workspace,
        "dispatch": agent._guarded_dispatch(routing, "task-1", 1, ctx, None, "call-1"),
    }
    for name in ("read_only_probe_tool",):
        await tool_manager.unregister(name)


@pytest_asyncio.fixture
async def plans(monkeypatch):
    """A plan manager of this test's own, and a hook manager that will actually run.

    `PlanManagerServer` is a singleton; constructing one normally hands back the
    process-wide instance and leaves an active gate behind for every later test.

    `hook_manager` is a no-op until something initializes it, so without the second half
    of this fixture the gate under test never runs and the program's write goes through —
    which is the failure this file is about, arriving for the wrong reason.
    """
    manager = PlanManagerServer.__new__(PlanManagerServer)
    manager._states = {}
    monkeypatch.setattr("agentevolver.plan.server.plan_manager", manager)

    hooks = HookContextManager()
    await hooks.initialize(hook_names=["plan_mode_hook"])
    monkeypatch.setattr(hook_manager, "hook_context_manager", hooks)
    return manager


async def run_program(bench, code):
    """Run one program the way the agent's dispatch hands it over."""
    return await RunCodeTool()(code=code, description="a test program",
                               ctx=bench["ctx"], sub_dispatch=bench["dispatch"])


# --------------------------------------------------------------------------- #
# The program's world
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_only_what_a_program_prints_or_returns_comes_back():
    """The saving is in what does *not* come back.

    A transport that returned every intermediate value would cost exactly what one call
    per turn costs, minus the round trips — which is most of the reason to write a
    program in the first place.
    """
    result = await code_runtime.run(
        "secret = 'x' * 5000\nprint('kept')\nreturn 'also kept'", {}, timeout=30)

    assert result.success
    assert result.logs == ["kept"]
    assert result.value == "also kept"
    assert "xxxxx" not in result.as_message()


@pytest.mark.asyncio
async def test_a_program_runs_in_an_interpreter_of_its_own():
    """Not a sandbox — an address space.

    Run in this process, the program would be one `import agentevolver.permission` away
    from the manager that is supposed to be checking it, and could switch the guard off
    before calling the thing it guards. The separate interpreter is what makes the
    framework objects unreachable rather than merely unmentioned.
    """
    result = await code_runtime.run("import os\nreturn os.getpid()", {}, timeout=30)

    assert result.success
    assert result.value != os.getpid()


@pytest.mark.asyncio
async def test_a_run_that_expires_still_returns_what_it_printed():
    """A timeout that reports only "timed out" throws away the work that got done.

    Output travels as it is printed for this reason: the last line a program managed to
    print is usually the one that says where it got stuck.
    """
    started = time.monotonic()
    result = await code_runtime.run(
        "print('got this far')\nimport time\ntime.sleep(30)", {}, timeout=2)
    elapsed = time.monotonic() - started

    assert result.failure.kind is CodeFailureKind.TIMEOUT
    assert result.logs == ["got this far"]
    # The budget is a promise. A polite wait for a program that has already overrun it
    # turns a 2-second timeout into whatever the grace period is.
    assert elapsed < 6


@pytest.mark.asyncio
async def test_independent_calls_overlap_instead_of_queueing():
    """Four half-second reads in one program should cost about half a second.

    Serialized bindings would still be correct and would still beat four turns, so the
    defect would never show up as a failure — only as a program that is four times
    slower than the model was told to expect.
    """
    async def slow(_args):
        await asyncio.sleep(0.4)
        return "done"

    started = time.monotonic()
    result = await code_runtime.run(
        "import asyncio\n"
        "return await asyncio.gather(*[tools.slow_tool(i=i) for i in range(4)])",
        {"slow_tool": slow}, timeout=30)
    elapsed = time.monotonic() - started

    assert result.value == ["done"] * 4
    assert elapsed < 1.2


@pytest.mark.asyncio
async def test_one_failed_call_is_the_programs_to_catch_not_the_runs_end():
    """A refusal has to be a value the program can branch on.

    If a failed binding killed the run, a program looping over ten files would lose the
    nine it could read because of the one it could not — and the model would have to
    re-request them one at a time, which is the cost this tool exists to avoid.
    """
    async def refuse(_args):
        raise PermissionError("nope")

    result = await code_runtime.run(
        "try:\n"
        "    await tools.refusing_tool()\n"
        "except ToolCallError as error:\n"
        "    print(f'caught {error} from {error.tool_name}')\n"
        "return 'kept going'",
        {"refusing_tool": refuse}, timeout=30)

    assert result.success
    assert result.logs == ["caught nope from refusing_tool"]
    assert result.value == "kept going"


# --------------------------------------------------------------------------- #
# Every call from a program takes the guarded path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_tool_that_refuses_a_wire_call_refuses_a_program_the_same_way(bench):
    """The check that stops a write outside the workspace is inside `write_file_tool`.

    It is reached only by calling that tool through `tool_manager`, which is what the
    agent's dispatch does. A binding that invoked the tool instance directly, or that
    built its own call path, would write the file — and every other test here would
    still pass.
    """
    outside = "/etc/agentevolver_code_mode_probe.txt"
    inside = bench["workspace"] / "written.txt"

    result = await run_program(bench, f"""
try:
    await tools.write_file_tool(path={outside!r}, content='x')
    print('NOT REFUSED')
except ToolCallError as error:
    print(f'refused: {{error}}')
await tools.write_file_tool(path={str(inside)!r}, content='inside')
""")

    assert "Permission denied" in result.message
    assert "NOT REFUSED" not in result.message
    assert not os.path.exists(outside)
    # And the same tool, called the same way, still works where it is allowed to — so
    # the refusal above is the permission check and not a broken binding.
    assert inside.read_text(encoding="utf-8") == "inside"


@pytest.mark.asyncio
async def test_plan_mode_stops_a_mutating_call_made_from_inside_a_program(bench, plans):
    """Plan mode is a hook on the agent's action path, not a check inside any tool.

    So this is the failure that a private dispatch path produces and nothing else
    catches: the gate is closed, the model writes a program, and the program edits the
    repository while the plan it is supposed to be waiting on has not been approved.
    """
    plans.enter(bench["ctx"].id)

    result = await run_program(bench, f"""
try:
    await tools.write_file_tool(path={str(bench['workspace'] / 'planned.txt')!r}, content='x')
    print('WROTE ANYWAY')
except ToolCallError as error:
    print(f'gated: {{error}}')
print(await tools.read_only_probe_tool(value='reading is fine'))
""")

    assert "WROTE ANYWAY" not in result.message
    assert "plan mode" in result.message
    assert not (bench["workspace"] / "planned.txt").exists()
    # The gate admits what declared itself effect-free, here as anywhere else. A program
    # refused every call would just be plan mode banning programs, which is a different
    # and much blunter rule than the one being tested.
    assert "probe saw reading is fine" in result.message


@pytest.mark.asyncio
async def test_a_program_may_not_start_another_program_or_finish_the_task(bench):
    """Two names are withheld from the bindings, for unrelated reasons.

    `run_code_tool` because a program starting a program recurses with nothing bounding
    it. `done_tool` because completion is read from a dispatched action: a `done` inside
    a program would be answered to the program, so the model would believe the task was
    finished while the loop carried on without a result.
    """
    assert RUN_CODE_TOOL not in bench["dispatch"].names
    assert "done_tool" not in bench["dispatch"].names

    result = await run_program(bench, """
for name in ('run_code_tool', 'done_tool'):
    try:
        await tools[name](code='pass')
    except ToolCallError as error:
        print(f'{name}: refused')
""")

    assert result.message.count("refused") == 2


@pytest.mark.asyncio
async def test_without_an_agents_dispatch_a_program_can_call_nothing(bench):
    """There is deliberately no fallback to `tool_manager`.

    The tempting alternative — "no agent, so call the manager directly" — would be an
    unchecked path to every tool that appears only outside a dispatch, which is exactly
    where nobody looks. Called with no dispatch, the tool binds an empty table.
    """
    result = await RunCodeTool()(
        code="return await tools.read_only_probe_tool(value='x')",
        ctx=bench["ctx"])

    assert not result.success
    assert "No tools were callable" in result.message
    assert "read_only_probe_tool" in result.message  # the program's own error names it


def test_only_the_code_transport_is_handed_a_way_to_dispatch():
    """The bridge is passed by name, to one tool. Read it from the source.

    A behavioural test cannot show the *absence* of the argument for every other tool,
    and passing it to all of them would hand any tool a way to dispatch further actions —
    a much larger claim than this design makes.
    """
    source = inspect.getsource(Agent._run_one)

    assert "route[1] == RUN_CODE_TOOL" in source
    assert "self._guarded_dispatch(" in source
    # And the dispatcher must be the agent's own per-action method, not a fresh path.
    dispatcher = inspect.getsource(Agent._guarded_dispatch)
    assert "await self._run_one(" in dispatcher
    assert "tool_manager" not in dispatcher


@pytest.mark.asyncio
async def test_a_blocked_call_is_an_error_and_not_an_empty_result(bench, monkeypatch):
    """A hook that blocks returns no output and no error; the program must not read that
    as "the tool ran and had nothing to say".

    That reading is the dangerous one: the program carries on as if the write happened,
    and reports success for work that was refused.
    """
    async def blocked(*args, **kwargs):
        return {"name": "write_file_tool", "done": False, "result": None,
                "reasoning": None, "error": None, "output": None}

    monkeypatch.setattr(Agent, "_run_one", blocked)
    dispatch = bench["agent"]._guarded_dispatch(
        bench["routing"], "task-1", 1, bench["ctx"], None, "call-9")

    with pytest.raises(RuntimeError, match="blocked"):
        await dispatch.call("write_file_tool", {"path": "x", "content": "y"})


# --------------------------------------------------------------------------- #
# What the model is told it may call
# --------------------------------------------------------------------------- #
def test_a_declaration_puts_required_arguments_before_optional_ones():
    """A signature is read as an example of the call, so its order is the call's order.

    Rendering `path: str = ..., content: str` would model a call that cannot be written,
    and a model copying the shape produces arguments the tool then rejects.
    """
    rendered = signature({"function": {
        "name": "write_file_tool",
        "parameters": {
            "type": "object",
            "properties": {"content": {"type": "string"}, "path": {"type": "string"},
                           "mode": {"type": "integer"}},
            "required": ["path", "content"],
        },
    }})

    assert rendered == "async def write_file_tool(*, path: str, content: str, mode: int = ...) -> str"


def test_the_declarations_carry_each_tools_one_line_summary():
    """The full parameter documentation is already in the prompt, one section up.

    Repeating it here would double the tool context for every agent holding the
    transport — the cost the reference implementation warns about — to say the same
    thing twice.
    """
    block = render_sdk([{"function": {"name": "grep_search_tool",
                                      "description": "Search  files\n for a pattern.",
                                      "parameters": {"type": "object", "properties": {}}}}])

    assert block == "# Search files for a pattern.\nasync def grep_search_tool() -> str"


def test_the_section_is_written_only_for_an_agent_that_holds_the_transport():
    """A calling convention for a tool the agent does not have is prompt it pays for
    every step and can never use."""
    assert code_mode_section("") == ""
    assert "run_code_tool" in code_mode_section("async def x() -> str")


@pytest.mark.asyncio
async def test_the_declarations_reach_the_model_beside_the_tool_cards(bench):
    """The block is assembled with the tool context, from the same roster.

    Generated somewhere the prompt never reads, it is dead text: the model would be
    holding a transport it was never told how to address, and would either not use it or
    invent a calling convention. Rendered for every agent instead, it is a cost paid by
    agents that cannot run a program at all.
    """
    agent = bench["agent"]

    assert await agent._code_mode_section(["write_file_tool"]) == ""

    section = await agent._code_mode_section(["write_file_tool", RUN_CODE_TOOL])
    assert "async def write_file_tool(*, path: str, content: str) -> str" in section
    # The transport declares itself as a tool, not as something a program may call.
    assert "async def run_code_tool(" not in section


def test_the_names_declared_are_the_names_that_bind(bench):
    """One list, read twice. Declaring a tool the bindings withhold sends the model to
    write a program that fails on a name it was told it had."""
    declared = callable_names([name for name, route in bench["routing"].items()
                               if route[0] == "tool"])

    assert list(bench["dispatch"].names) == declared
    assert set(UNCALLABLE).isdisjoint(declared)


# --------------------------------------------------------------------------- #
# Declarations other machinery reads
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_program_is_not_declared_free_of_effects(bench):
    """`mutates=False` would be a claim about code nobody has written yet.

    The plan gate admits an action on that field alone, so declaring a transport for
    arbitrary programs effect-free would open the gate for everything reachable through
    it — the one hole this whole file exists to keep shut.
    """
    declaration = await declaration_of("tool", RUN_CODE_TOOL)

    assert declaration["mutates"] is None
    assert action_is_allowed("tool", RUN_CODE_TOOL, declaration) is False


def test_the_transport_name_is_the_one_the_agent_looks_for():
    """The name is written twice: as a literal on the tool, because the registration
    check and the catalog generator read it from source without importing it, and as a
    constant, because the agent recognises the one dispatch it hands a dispatcher to. A
    rename that changes one leaves the transport with no bindings and no error."""
    assert RunCodeTool.model_fields["name"].default == RUN_CODE_TOOL


def test_the_programs_own_budget_is_smaller_than_the_call_budget():
    """The tool has to be the one that reports its timeout.

    Cut off by the manager instead, the call comes back as "tool timed out" with none of
    the output the program had already produced — and that output is how the model
    learns which part was slow.
    """
    tool = RunCodeTool()

    assert tool.timeout < tool.call_timeout_seconds


def test_the_module_documents_the_route_a_call_takes():
    """The route is the design, and it is spread over three files.

    Whoever reads any one of them sees only a piece: the tool sees a callable, the agent
    sees an argument, the runtime sees a binding. The README is where the whole path is
    written down, so it is the thing that must not go stale.
    """
    readme = (Path(__file__).parents[1] / "agentevolver" / "tool" / "default"
              / "code_mode" / "README.md").read_text(encoding="utf-8")

    for step in ("plan_mode_hook", "permission_manager.check", "_run_one"):
        assert step in readme
