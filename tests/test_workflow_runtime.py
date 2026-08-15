"""HTML a model wrote becomes a runnable program only if the compiler and the budgets agree.

A workflow is authored as a constrained HTML document, so everything standing between
that document and a live fan-out of agents lives here: the compiler that refuses scripts,
event handlers and unbounded loops; the runtime that shares one Agent budget across
nested runs, checkpoints after every step, and resumes without paying again for work that
already finished; and the registry that calls a workflow healthy only on evidence from
real, terminal runs.

Each of those is a place where the mistake is not recoverable after the fact. A loop
without `max-rounds` never terminates. A per-run budget lets a nested workflow spend ten
times its parent's allowance. A resume that re-invokes completed agents pays for every
model call a second time and repeats their side effects. And a health summary computed
from evidence the caller supplied rather than from runs that happened is the number
self-evolution uses to decide what to keep.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agentevolver.config import config
from agentevolver.paths import P, path_manager
from agentevolver.workflow import (
    ExecutionState, InvocationState, WorkflowCompileError, WorkflowEvaluation,
    WorkflowContextManager, WorkflowRuntime, WorkflowState, WorkflowStatus,
    WorkflowRun, workflow_compiler, workflow_manager, workflow_runtime,
)


class FakeRuntime(WorkflowRuntime):
    """The real runtime with only its two outward edges replaced.

    ``_invoke`` is where a step reaches an agent or a tool manager, and
    ``_validate_capabilities`` is where preflight insists those targets are registered.
    Stubbing exactly those two — and nothing else — means every assertion below is about
    the orchestration the runtime actually performs: frames, budgets, retries,
    checkpoints. ``kinds`` and ``calls`` record what the runtime asked for, so a test can
    check that a step was dispatched twice rather than once.
    """

    def __init__(self, handler):
        super().__init__()
        self.handler = handler
        self.calls = []
        self.kinds = []

    async def _validate_capabilities(self, definition):
        return None

    async def _invoke(self, capability_type, target, task, args, ctx, depth, budget=None):
        self.kinds.append(capability_type)
        self.calls.append((target, task, args))
        value = self.handler(target, task, args)
        return await value if asyncio.iscoroutine(value) else value


def retain_successful_run(definition, run_id, token_cost=0):
    """Plant a terminal run in the runtime's table so an evaluation has something to cite.

    ``record_evaluation`` refuses a success that names no retained run, and reads elapsed
    time and token cost off that run rather than off the caller. Tests that exercise the
    evidence rules therefore need a run to exist; the fixed timestamps make the derived
    ``elapsed_ms`` exactly one second.
    """
    run = WorkflowRun(
        id=run_id, workflow_name=definition.name, workflow_version=definition.version,
        program_hash=definition.program_hash, state=WorkflowState.SUCCEEDED,
        token_cost=token_cost,
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:01+00:00",
    )
    workflow_runtime._runs[run_id] = run
    return run


# A workflow using every fan-out form: map over files, verify each finding, reduce to one
# report. Several tests derive variants from it by string replacement.
HTML = """
<workflow name="dynamic_audit" version="1.2.0" description="Dynamic file audit"
          max-agents="20" max-concurrency="4" enable-evolving="true">
  <inputs><input name="files" type="array" required="true" /></inputs>
  <applicability><tag>audit</tag><tag>parallel</tag>Use for many files.</applicability>
  <flow>
    <map id="audits" items="${inputs.files}" as="file" concurrency="3">
      <agent id="audit" name="audit_agent" task="Audit ${file}">
        <arg name="path" value="${file}" />
      </agent>
    </map>
    <verify id="verified" items="${audits}" as="finding" agent="verify_agent"
            task="Verify ${finding}" concurrency="2" />
    <reduce id="summary" items="${verified}" agent="summary_agent" task="Summarize" />
  </flow>
  <outputs><output name="report" value="${summary.data}" /></outputs>
</workflow>
"""


def complete_html(workflow_source):
    """Wrap a fragment as a full document — persisted workflows must carry a DOCTYPE."""
    return f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>{workflow_source}</body></html>"


# --------------------------------------------------------------------------- #
# Turning a document into a program
# --------------------------------------------------------------------------- #
def test_the_authored_order_and_metadata_survive_compilation():
    """The document is the source of truth; compilation must not reorder or evaluate it.

    Step order is the program: `map → verify → reduce` reads results forward, and any
    other order silently summarizes findings nobody verified. The nested task text is
    asserted still holding `${file}` because templates are resolved per item at run time —
    a compiler that expanded them once would give every map item the same task.
    """
    definition = workflow_compiler.compile(HTML)
    assert definition.name == "dynamic_audit"
    assert definition.tags == ["audit", "parallel"]
    assert definition.enable_evolving is True
    assert [step.type.value for step in definition.program] == ["map", "verify", "reduce"]
    assert definition.program[0].children[0].task == "Audit ${file}"


def test_the_compiler_refuses_what_it_cannot_bound_or_trust():
    """Workflow HTML is model-authored, so this is the only barrier in front of it.

    Each case is a different way the document stops being a bounded program. A `<script>`
    or an `onclick` would execute in whatever renders the preview; a remote `src` sources
    that code from elsewhere. A `<loop>` without `max-rounds` never terminates, and
    `max-agents="1001"` is a spend cap written above its ceiling. A future
    `schema-version` means the tags do not mean what this compiler thinks they mean, and
    a `version` of `latest` cannot be compared, archived, or rolled back to. The last two
    are subtler: an input whose declared type contradicts its JSON Schema, and an
    output referencing a step that only runs inside a branch — the run would end
    successfully with an output that does not exist.
    """
    with pytest.raises(WorkflowCompileError, match="renderer script"):
        workflow_compiler.compile('<workflow name="bad"><flow><script>evil()</script></flow></workflow>')
    with pytest.raises(WorkflowCompileError, match="bounded"):
        workflow_compiler.compile('<workflow name="bad"><flow><loop id="x"><agent name="a"/></loop></flow></workflow>')
    with pytest.raises(WorkflowCompileError, match="max-agents"):
        workflow_compiler.compile('<workflow name="bad" max-agents="1001"><flow><agent name="a"/></flow></workflow>')
    with pytest.raises(WorkflowCompileError, match="schema-version"):
        workflow_compiler.compile('<workflow name="future" schema-version="2.0.0"><flow><agent name="a"/></flow></workflow>')
    with pytest.raises(WorkflowCompileError, match="version"):
        workflow_compiler.compile('<workflow name="bad" version="latest"><flow><agent name="a"/></flow></workflow>')
    with pytest.raises(WorkflowCompileError, match="Event handler"):
        workflow_compiler.compile('<workflow name="bad" onclick="evil()"><flow><agent name="a"/></flow></workflow>')
    with pytest.raises(WorkflowCompileError, match="Remote"):
        workflow_compiler.compile('''
          <html><body><script src="https://evil.example/visual/js/workflow.js"></script>
          <workflow name="bad"><flow><checkpoint /></flow></workflow></body></html>
        ''')
    with pytest.raises(WorkflowCompileError, match="conflicts"):
        workflow_compiler.compile('''
          <workflow name="bad"><inputs><input name="items" type="string" />
          <schema for="items">{"type":"array"}</schema></inputs>
          <flow><checkpoint id="saved" /></flow></workflow>
        ''')
    with pytest.raises(WorkflowCompileError, match="guaranteed top-level"):
        workflow_compiler.compile('''
          <workflow name="bad"><flow><branch id="choice" test="${inputs.flag}">
          <then><agent id="conditional" name="worker" /></then></branch></flow>
          <outputs><output name="result" value="${conditional}" /></outputs></workflow>
        ''')


# --------------------------------------------------------------------------- #
# What the registry knows, and what it tells the model
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_registry_projects_what_it_discovers_and_reloads_the_evidence_it_wrote(tmp_path):
    """Discovery, roster, schemas, cache invalidation, and evidence are one lifecycle.

    Two of these are easy to get backwards. An empty allowlist means *nothing is
    permitted*, not *no filter given* — reading it as the latter hands a restricted agent
    the whole catalogue. And the roster is cached, so a workflow registered after the
    first render is invisible to the model until the cache is dropped; the symptom is a
    workflow that exists and cannot be called.

    The second manager is built over the same evidence file to prove the record reached
    disk rather than a dictionary: evaluation history is what a later rollback decision
    reads, and it has to outlive the process that produced it.
    """
    builtins = tmp_path / "builtins"
    builtins.mkdir()
    (builtins / "dynamic_audit.html").write_text(complete_html(HTML), encoding="utf-8")
    evidence = tmp_path / "evaluations.json"
    context = WorkflowContextManager(builtin_dir=builtins, evaluation_path=evidence)

    await context.initialize()
    assert context.list() == ["dynamic_audit"]
    assert "dynamic_audit" in context.get_instruction()
    assert context.get_instruction(allowlist=[]) == ""
    schemas = await context.function_callings()
    assert schemas[0][0]["function"]["name"] == "workflow__dynamic_audit"
    assert schemas[0][1] == ("workflow", "dynamic_audit")

    context.register(HTML.replace('name="dynamic_audit"', 'name="second_flow"'), override=True)
    assert "second_flow" in context.get_instruction()  # registration invalidated the cache
    v2 = context.register(
        HTML.replace('version="1.2.0"', 'version="1.3.0"'), override=True,
    )
    assert v2.version == "1.3.0"
    assert context.restore("dynamic_audit", "1.2.0").version == "1.2.0"
    retain_successful_run(context.get("dynamic_audit"), "context-test")
    context.record_evaluation(WorkflowEvaluation(
        workflow_name="dynamic_audit", workflow_version="1.2.0",
        run_id="context-test", success=True, quality_score=0.9,
    ))
    assert evidence.exists()

    restored = WorkflowContextManager(builtin_dir=builtins, evaluation_path=evidence)
    await restored.initialize()
    assert restored.evaluation_summary("dynamic_audit")["runs"] == 1
    await restored.cleanup()
    assert restored.list() == []
    workflow_runtime._runs.pop("context-test", None)


@pytest.mark.asyncio
async def test_every_active_workflow_is_callable_by_name_rather_than_through_a_runner():
    """A workflow is projected as its own function, not as an argument to a generic one.

    The absent names are the point of the assertion. When the model had to call
    `search_workflows` and then `run_dynamic_workflow(name=...)`, choosing a workflow was
    a two-step guess against a string it had to remember; as `workflow__dynamic_audit` it
    is one entry in the same schema list as every other capability, and the route tuple
    tells the dispatcher where to send it. If those helper names ever reappear, the
    indirection is back and this projection has stopped being the interface.
    """
    active = workflow_manager.register(HTML, override=True)
    second = workflow_manager.register(HTML.replace('name="dynamic_audit"', 'name="second_audit"'), override=True)
    try:
        assert set(item.name for item in workflow_manager.search("parallel")) == {active.name, second.name}
        schemas = await workflow_manager.function_callings()
        names = {entry[0]["function"]["name"] for entry in schemas}
        assert "workflow__dynamic_audit" in names
        assert "workflow__second_audit" in names
        assert not names & {
            "search_workflows", "run_dynamic_workflow", "register_workflow_candidate",
        }
        assert next(route for schema, route in schemas if schema["function"]["name"] == "workflow__dynamic_audit") == ("workflow", "dynamic_audit")
    finally:
        workflow_manager.unregister(active.name)
        workflow_manager.unregister(second.name)


@pytest.mark.asyncio
async def test_the_roster_names_workflows_and_the_html_is_fetched_only_when_asked_for():
    """Every registered workflow's HTML in the prompt would crowd out the task itself.

    The roster carries name, version, inputs and tags — enough to choose — and the
    assertion that `<workflow` does not appear in it is what keeps a document from leaking
    back into that budget. `inspect_workflow` is the second step for an agent that has
    chosen and now needs the program.
    """
    from agentevolver.tool.default.inspect_workflow import InspectWorkflow

    definition = workflow_manager.register(HTML, override=True)
    try:
        roster = workflow_manager.get_instruction()
        assert definition.name in roster
        assert "<workflow" not in roster

        response = await InspectWorkflow()(name=definition.name)
        assert response.success
        assert response.data["html"].lstrip().startswith("<workflow")
        assert response.data["nodes"][0]["type"] == "map"
        assert response.data["source_path"] is None
    finally:
        workflow_manager.unregister(definition.name)


# --------------------------------------------------------------------------- #
# Running a program
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_fan_out_flow_runs_its_items_concurrently_and_checkpoints_outside_the_workspace(tmp_path):
    """`concurrency="3"` has to mean three at once, and the checkpoint is not the agent's file.

    `peak` is the load-bearing assertion: a runtime that awaited each map item in turn
    produces exactly the same output and the same agent count, and the only visible
    difference is that the three audits never overlap — a `map` that is a for-loop wearing
    a concurrency attribute, three times slower than the document claims.

    The checkpoint lives in the framework layout because it is bookkeeping, not the
    agent's work; written into the workspace it lands among the files the agent is being
    asked to produce, and with no workspace configured it lands in the current directory
    entirely outside `output/`.
    """
    config.workspace_root = str(tmp_path)
    active = peak = 0

    async def handler(target, task, args):
        nonlocal active, peak
        if target == "audit_agent":
            active += 1
            peak = max(peak, active)
            # Long enough that serial execution could not overlap by accident.
            await asyncio.sleep(0.01)
            active -= 1
            return {"path": args["path"], "issue": True}
        if target == "verify_agent":
            return {"accepted": True, "finding": args["finding"]}
        return {"data": {"count": len(args["items"])}}

    runtime = FakeRuntime(handler)
    run = await runtime.run(
        workflow_compiler.compile(HTML), input={"files": ["a.py", "b.py", "c.py"]},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    assert run.state == WorkflowState.SUCCEEDED
    assert run.output == {"report": {"count": 3}}
    # Three audits, three verifications of their findings, one reduce.
    assert run.agent_count == 7
    assert peak == 3
    saved = json.loads(Path(run.checkpoint_path).read_text())
    # Bookkeeping lives in the layout, never inside the agent's workspace.
    assert Path(run.checkpoint_path).parent == path_manager.get(P.CHECKPOINTS)
    assert saved["state"] == "succeeded"


@pytest.mark.asyncio
async def test_each_verification_vote_is_its_own_agent_call(tmp_path):
    """`min-votes="2"` must buy two opinions, not one opinion recorded twice.

    The agent count is the check that cannot be faked by the verdict list: two loop rounds
    produce two results, and two independent votes on each brings the total to six. A
    runtime that called the reviewer once and duplicated its answer would still produce
    two verdicts per item and would make every verification unanimous by construction —
    which is the failure mode independent verification exists to rule out.
    """
    checks = 0
    source = """
    <workflow name="votes" max-concurrency="2">
      <inputs><input name="keep_going" type="boolean" required="true" /></inputs>
      <flow>
        <loop id="rounds" max-rounds="2" while="${inputs.keep_going}">
          <agent id="work" name="worker" />
        </loop>
        <verify id="votes" items="${rounds}" as="result" agent="reviewer" min-votes="2" />
      </flow>
    </workflow>
    """
    def handler(target, task, args):
        nonlocal checks
        checks += 1
        return {"target": target, "n": checks}
    runtime = FakeRuntime(handler)
    run = await runtime.run(
        workflow_compiler.compile(source), input={"keep_going": True},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    assert run.successful
    assert run.agent_count == 6  # two workers + two independent votes for each result
    assert all(len(item["verdicts"]) == 2 for item in run.variables["votes"])


@pytest.mark.asyncio
async def test_an_input_that_breaks_the_declared_schema_is_rejected_before_anything_runs(tmp_path):
    """REJECTED, not FAILED: nothing ran, so nothing has to be undone.

    The two cases fail for opposite reasons. `["one"]` satisfies `type: array` and
    violates `minItems: 2`, so a runtime that only checked the coarse HTML `type`
    attribute would admit it. The `unexpected` key is the one people expect to be ignored
    — `additionalProperties: false` makes a misspelled input name an error instead of a
    silently dropped argument, which is the difference between a visible rejection and a
    workflow that runs with a default it was never asked to use.
    """
    source = """
    <workflow name="schema_guard">
      <inputs>
        <input name="files" type="array" required="true" />
        <schema for="files">{"type":"array","items":{"type":"string"},"minItems":2}</schema>
      </inputs>
      <flow><checkpoint id="saved" /></flow>
    </workflow>
    """
    runtime = FakeRuntime(lambda *_: None)
    definition = workflow_compiler.compile(source)
    too_short = await runtime.run(
        definition, input={"files": ["one"]},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    assert too_short.state == WorkflowState.REJECTED
    extra = await runtime.run(
        definition, input={"files": ["one", "two"], "unexpected": True},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    assert extra.state == WorkflowState.REJECTED


@pytest.mark.asyncio
async def test_a_step_that_outruns_its_timeout_is_retried_and_then_gives_up(tmp_path):
    """A hung step must not become a hung run, and the retry has to be a real second call.

    The handler sleeps for a second against a `timeout="0.01"`, so the only way out is the
    timeout firing. `kinds` holding the step type twice is what separates "retried" from
    "recorded a retry": with `retries="1"` the step is dispatched again, and the two
    attempts on the invocation are the audit trail a reader needs to tell one slow call
    from two.
    """
    async def slow(*_):
        await asyncio.sleep(1)

    runtime = FakeRuntime(slow)
    definition = workflow_compiler.compile("""
      <workflow name="timed">
        <flow><environment id="render" name="browser" action="open"
          timeout="0.01" retries="1" retry-delay="0" /></flow>
      </workflow>
    """)
    run = await runtime.run(definition, ctx=SimpleNamespace(workspace_root=str(tmp_path)))
    assert run.state == WorkflowState.FAILED
    assert runtime.kinds == [definition.program[0].type, definition.program[0].type]
    invocation = next(iter(run.invocations.values()))
    assert len(invocation.attempts) == 2


@pytest.mark.asyncio
async def test_a_retried_step_keeps_both_attempts_and_the_untaken_branch_is_recorded_as_skipped(tmp_path):
    """Two things a trace must not quietly lose: the failure that was retried, and the road not taken.

    The first attempt raises and the second succeeds, so the invocation ends COMPLETED —
    and a runtime that overwrote the attempt list would report a step that worked first
    time. Reading the trace afterwards, a flaky capability would look reliable.

    The skipped frame matters for the same reason: if the untaken branch leaves no record,
    the trace does not show that a choice was made, only that one path exists. The opening
    empty-input rejection is here because the same run object has to survive both the
    rejection path and the execution path.
    """
    rejected = await FakeRuntime(lambda *_: None).run(
        workflow_compiler.compile(HTML), input={},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    assert rejected.state == WorkflowState.REJECTED

    attempts = 0
    source = """
    <workflow name="retry">
      <flow>
        <agent id="unstable" name="worker" retries="1" />
        <branch id="choice" test="${unstable.data.ok}">
          <then><agent id="chosen" name="worker" /></then>
          <else><agent id="not_chosen" name="worker" /></else>
        </branch>
      </flow>
    </workflow>
    """
    def handler(target, task, args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return {"data": {"ok": True}}
    run = await FakeRuntime(handler).run(
        workflow_compiler.compile(source),
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    unstable = next(item for item in run.invocations.values() if item.key.endswith(":unstable"))
    assert [item.state for item in unstable.attempts] == [InvocationState.RETRYING, InvocationState.COMPLETED]
    assert any(frame.state == ExecutionState.SKIPPED for frame in run.frames.values())


# --------------------------------------------------------------------------- #
# Stopping, resuming, and running in the background
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_resuming_replays_completed_agents_from_the_checkpoint_instead_of_calling_them_again(tmp_path):
    """`calls` staying at 3 across the resume is the whole test.

    Resume looks correct either way — the second run succeeds and returns the same answer
    whether or not it re-invoked the checker and the fixer. What differs is that every
    completed agent call is paid for twice, and any side effect those agents had happens
    twice. The state assertions name the mechanism that avoids it: invocations come back
    CACHED and their frames with them, so a reader can tell replayed work from repeated
    work.
    """
    calls = 0
    source = """
    <workflow name="repair">
      <flow>
        <loop id="repair_loop" max-rounds="3" until="${check.success}">
          <agent id="check" name="checker" />
          <branch id="fix_if_needed" test="not check.success">
            <then><agent id="fix" name="fixer" /></then>
          </branch>
        </loop>
      </flow>
    </workflow>
    """
    def handler(target, task, args):
        nonlocal calls
        calls += 1
        # The checker only reports success on the third round, so the loop must
        # actually iterate rather than exit on its first `until` evaluation.
        return {"success": calls >= 3} if target == "checker" else {"fixed": True}
    runtime = FakeRuntime(handler)
    definition = workflow_compiler.compile(source)
    run = await runtime.run(definition, ctx=SimpleNamespace(workspace_root=str(tmp_path)))
    assert run.successful
    assert calls == 3
    resumed = await runtime.resume(definition, run.checkpoint_path, ctx=SimpleNamespace(workspace_root=str(tmp_path)))
    assert resumed.successful
    assert calls == 3  # completed agent invocations came from checkpoint cache
    assert all(item.state == InvocationState.CACHED for item in resumed.invocations.values())
    assert any(item.state == ExecutionState.CACHED for item in resumed.frames.values())


@pytest.mark.asyncio
async def test_a_checkpoint_refuses_a_program_that_changed_underneath_it(tmp_path):
    """Cached results belong to the program that produced them, and to no other.

    The edit here is one word of task text — the sort of change nobody thinks of as
    breaking, and exactly why the guard is on a hash of the executable contract rather
    than on name and version. Without it, resume would replay the old summarizer's output
    into a workflow that now asks for something different, and the run would succeed while
    answering the previous question.

    The frame-parentage assertion covers the other half of a usable checkpoint: every
    frame's parent must exist in the same run, or the recorded hierarchy cannot be walked
    back to the step that created it.
    """
    def handler(target, task, args):
        if "items" in args:
            return {"data": {"count": len(args["items"])}}
        return {"ok": True}

    runtime = FakeRuntime(handler)
    definition = workflow_compiler.compile(HTML)
    run = await runtime.run(
        definition, input={"files": ["a.py", "b.py"]},
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    assert run.successful
    assert all(
        frame.parent_key is None or frame.parent_key in run.frames
        for frame in run.frames.values()
    )
    changed = workflow_compiler.compile(HTML.replace("Summarize", "Summarize differently"))
    with pytest.raises(ValueError, match="executable contract"):
        await runtime.resume(changed, run.checkpoint_path, ctx=SimpleNamespace(workspace_root=str(tmp_path)))
    assert runtime.list_runs(definition.name)[0].id == run.id
    assert runtime.discard_run(run.id)
    assert runtime.get_run(run.id) is None


@pytest.mark.asyncio
async def test_a_paused_run_continues_from_where_it_stopped(tmp_path):
    """Pause is a request honoured at the next boundary, so PAUSING and PAUSED differ.

    The first agent is held open until the test releases it. Asking for PAUSED right after
    `pause()` would be asking the runtime to abandon a call already in flight; PAUSING is
    the honest answer, and the polling loop is waiting for the step to reach its next
    control point. Reading the two as one state is how a pause that has not taken effect
    yet gets reported as a pause that has.

    Continuing must then finish the second step rather than restart the flow: both
    invocations end COMPLETED with one completed attempt each, which is what "continue"
    means as distinct from "run again".
    """
    started, release = asyncio.Event(), asyncio.Event()
    source = """
    <workflow name="controlled" schema-version="1.0.0">
      <flow>
        <agent id="first" name="worker" />
        <agent id="second" name="worker" />
      </flow>
    </workflow>
    """
    calls = 0
    async def handler(target, task, args):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return {"call": calls}

    runtime = FakeRuntime(handler)
    run_id = runtime.start(
        workflow_compiler.compile(source),
        ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    assert runtime.pause(run_id)
    assert runtime.get_run(run_id).state == WorkflowState.PAUSING
    release.set()
    # Up to a second of polling: the transition happens on the run's own task, not
    # on this one, so there is nothing here to await directly.
    for _ in range(100):
        if runtime.get_run(run_id).state == WorkflowState.PAUSED:
            break
        await asyncio.sleep(0.01)
    assert runtime.get_run(run_id).state == WorkflowState.PAUSED
    assert runtime.continue_run(run_id)
    for _ in range(100):
        if runtime.get_run(run_id).state == WorkflowState.SUCCEEDED:
            break
        await asyncio.sleep(0.01)
    run = runtime.get_run(run_id)
    assert run.state == WorkflowState.SUCCEEDED
    assert len(run.frames) == 2 and len(run.invocations) == 2
    assert all(item.state == InvocationState.COMPLETED for item in run.invocations.values())
    assert all(item.attempts[0].state == InvocationState.COMPLETED for item in run.invocations.values())


@pytest.mark.asyncio
async def test_background_start_is_immediately_visible_and_cleanup_releases_state(tmp_path):
    """`start()` returns an id that already answers, and shutdown does not leave a run behind.

    A caller that starts a run and immediately asks after it must not be told the id is
    unknown — that reads as "it failed to start" and invites a second launch of the same
    work. The handler here never returns, so cleanup is being asked to cancel something
    genuinely in flight; a `get_run` that still answered afterwards would mean the run
    table outlives the runs it describes.
    """
    entered = asyncio.Event()

    async def wait_forever(*_):
        entered.set()
        await asyncio.Event().wait()

    runtime = FakeRuntime(wait_forever)
    definition = workflow_compiler.compile('<workflow name="background"><flow><agent name="worker" /></flow></workflow>')
    run_id = runtime.start(definition, ctx=SimpleNamespace(workspace_root=str(tmp_path)))
    assert runtime.get_run(run_id).state == WorkflowState.CREATED
    await asyncio.wait_for(entered.wait(), timeout=1)
    await runtime.cleanup()
    assert runtime.get_run(run_id) is None


# --------------------------------------------------------------------------- #
# Budgets and nesting
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_two_workflows_that_call_each_other_are_refused_before_anything_runs(tmp_path):
    """Preflight walks the whole graph, because the cycle is only obvious from outside it.

    Neither `cycle_a` nor `cycle_b` is wrong on its own; the recursion exists only in the
    pair. Caught at run time instead, each level would already have spawned agents before
    the next one was entered, so the cost of the mistake is paid before it is noticed. The
    REJECTED state says nothing was started.
    """
    first = workflow_manager.register(
        '<workflow name="cycle_a"><flow><workflow name="cycle_b" /></flow></workflow>',
        override=True,
    )
    workflow_manager.register(
        '<workflow name="cycle_b"><flow><workflow name="cycle_a" /></flow></workflow>',
        override=True,
    )
    try:
        run = await WorkflowRuntime().run(
            first, ctx=SimpleNamespace(workspace_root=str(tmp_path)),
        )
        assert run.state == WorkflowState.REJECTED
        assert "Recursive Workflow invocation" in run.error
    finally:
        workflow_manager.unregister("cycle_a")
        workflow_manager.unregister("cycle_b")


@pytest.mark.asyncio
async def test_a_nested_workflow_spends_the_root_budget_not_its_own(tmp_path):
    """The child declares `max-agents="10"` and is allowed to spend what the parent has left.

    The parent's cap of 2 is the promise made to whoever started the run. If the budget
    were per-run, that promise would be worth nothing: any workflow could raise its own
    ceiling simply by delegating to a child that declared a larger one, and nesting two
    levels deep would multiply it again. The run fails rather than truncating, because a
    partial fan-out that silently stops is indistinguishable from one that found nothing.
    """
    child = workflow_compiler.compile("""
      <workflow name="budget_child" max-agents="10">
        <flow>
          <agent id="one" name="worker" />
          <agent id="two" name="worker" />
        </flow>
      </workflow>
    """)
    parent = workflow_compiler.compile("""
      <workflow name="budget_parent" max-agents="2">
        <flow>
          <agent id="root_agent" name="worker" />
          <workflow id="nested" name="budget_child" />
        </flow>
      </workflow>
    """)

    class NestedRuntime(FakeRuntime):
        """Resolve a nested <workflow> step to the compiled child, sharing the root budget."""

        async def _invoke(self, capability_type, target, task, args, ctx, depth, budget=None):
            if capability_type.value == "workflow":
                return await self.run(child, input=args, ctx=ctx, depth=depth + 1, _budget=budget)
            return await super()._invoke(capability_type, target, task, args, ctx, depth, budget=budget)

    run = await NestedRuntime(lambda *_: {"ok": True}).run(
        parent, ctx=SimpleNamespace(workspace_root=str(tmp_path)),
    )
    assert run.state == WorkflowState.FAILED
    assert "Agent budget" in run.error


# --------------------------------------------------------------------------- #
# The evidence self-evolution acts on
# --------------------------------------------------------------------------- #
def test_three_successful_runs_are_what_makes_a_workflow_healthy():
    """`healthy` gates keeping a workflow, so it must mean more than one lucky run.

    The threshold is three *distinct* cases at 0.8 success and 0.7 quality. One success
    proves a workflow can run, not that it works; a summary that flipped healthy on the
    first green result would let a workflow that happened to suit one input survive
    review, and would let the evolution loop stop improving it.
    """
    definition = workflow_manager.register(
        HTML.replace('name="dynamic_audit"', 'name="evaluated_workflow"'),
        override=True,
    )
    try:
        for index in range(3):
            retain_successful_run(definition, str(index))
            workflow_manager.record_evaluation(WorkflowEvaluation(
                workflow_name=definition.name, workflow_version=definition.version,
                run_id=str(index), success=True, quality_score=0.9,
            ))
        summary = workflow_manager.evaluation_summary(definition.name)
        assert summary["healthy"] is True
        assert definition.status == WorkflowStatus.ACTIVE
    finally:
        for index in range(3):
            workflow_runtime._runs.pop(str(index), None)
        workflow_manager.unregister(definition.name)


def test_evaluation_evidence_is_version_scoped():
    """A new version starts with no track record, however good the last one was.

    Three clean runs at 1.0 quality are recorded against v1.2.0, and then the workflow is
    rewritten as v1.3.0. Carrying that history forward would let an edit inherit the
    reputation of the code it replaced — the rewrite would read as healthy before it had
    ever run, which is precisely the moment evidence is needed.
    """
    v1 = workflow_manager.register(
        HTML.replace('name="dynamic_audit"', 'name="version_scoped"'),
        override=True,
    )
    try:
        for index in range(3):
            retain_successful_run(v1, f"v1-{index}")
            workflow_manager.record_evaluation(WorkflowEvaluation(
                workflow_name=v1.name, workflow_version=v1.version,
                run_id=f"v1-{index}", success=True, quality_score=1.0,
            ))
        v2 = workflow_manager.register(
            HTML.replace('name="dynamic_audit"', 'name="version_scoped"')
                .replace('version="1.2.0"', 'version="1.3.0"'),
            override=True,
        )
        assert workflow_manager.evaluation_summary(v2.name)["runs"] == 0
    finally:
        for index in range(3):
            workflow_runtime._runs.pop(f"v1-{index}", None)
        workflow_manager.unregister("version_scoped")


def test_an_evaluation_must_cite_a_real_run_and_may_cite_it_only_once():
    """The evaluator is an agent, so its numbers are claims until a run backs them.

    Three separate ways of manufacturing a good record are closed here. A success with no
    `run_id` is refused outright. A caller-declared `token_cost` of 999 is discarded in
    favour of the 25 the run actually spent, so cost cannot be reported as anything other
    than what happened — and `elapsed_ms` likewise comes from the run's own timestamps.
    Recording the same run twice is rejected, which matters because the health threshold
    counts distinct cases: without it, one good run submitted three times is a healthy
    workflow.
    """
    definition = workflow_manager.register(
        HTML.replace('name="dynamic_audit"', 'name="trusted_evidence"'), override=True,
    )
    try:
        with pytest.raises(ValueError, match="real Workflow run_id"):
            workflow_manager.record_evaluation(WorkflowEvaluation(
                workflow_name=definition.name, workflow_version=definition.version,
                success=True, quality_score=1.0,
            ))
        retain_successful_run(definition, "trusted-run", token_cost=25)
        evidence = WorkflowEvaluation(
            workflow_name=definition.name, workflow_version=definition.version,
            run_id="trusted-run", success=True, quality_score=1.0, token_cost=999,
        )
        recorded = workflow_manager.record_evaluation(evidence)
        assert recorded.case_id == "trusted-run"
        # One second between the planted run's start and finish timestamps.
        assert recorded.elapsed_ms == 1000
        assert recorded.token_cost == 25
        with pytest.raises(ValueError, match="already been evaluated"):
            workflow_manager.record_evaluation(evidence)
    finally:
        workflow_runtime._runs.pop("trusted-run", None)
        workflow_manager.unregister(definition.name)


def test_the_workflow_evaluator_can_read_and_record_but_not_change_what_it_grades():
    """A grader with write access can resolve a bad grade by editing the thing graded.

    The allowlist narrows the evaluator to the one workflow under review, and `read_only`
    is then qualified by exactly one exception: recording its own verdict. That the same
    tool's `rollback` action is refused is the point of the pair — permission here is per
    action, not per tool, so an evaluator cannot reach a mutation by going through a tool
    it is otherwise allowed to call.
    """
    from agentevolver.agent.evaluator.workflow_evaluate_agent import WorkflowEvaluateAgent

    evaluator = WorkflowEvaluateAgent(base_dir=".")
    assert evaluator._include_agents() is False
    assert evaluator._include_workflows() is True
    assert evaluator._target_capability_allowlists("parallel_review") == {
        "workflow_allowlist": ["parallel_review"],
    }
    assert evaluator.permission_mode == "read_only"
    assert evaluator._allow_read_only_tool_call(
        "evolution_tool", {"action": "record_workflow_evaluation"},
    )
    assert not evaluator._allow_read_only_tool_call(
        "evolution_tool", {"action": "rollback"},
    )


# --------------------------------------------------------------------------- #
# Getting an authored workflow into the registry
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_workflow_loaded_as_an_extension_remembers_the_file_it_came_from(tmp_path):
    """Without `source_path` the registry holds a program with no origin.

    Everything afterwards needs the path: archiving a version, rolling one back, showing
    an author where the document lives. A registration that compiled the file and then
    forgot where it read it from would look completely correct until the first rollback.
    """
    from agentevolver.extension.server import ExtensionManagerServer
    path = tmp_path / "audit.html"
    path.write_text(complete_html(HTML), encoding="utf-8")
    manager = ExtensionManagerServer(base_dir=str(tmp_path / "extensions"))
    name = await manager._load_component("workflow", str(path), None, None, None)
    try:
        assert workflow_manager.get(name).source_path == str(path.resolve())
    finally:
        workflow_manager.unregister(name)


@pytest.mark.asyncio
async def test_installing_a_workflow_keeps_the_live_file_and_archives_its_version(tmp_path):
    """Two copies with different jobs: the one that runs, and the one to go back to.

    The staged file stays where the runtime loads it from, and `.versions/workflow/
    saved_workflow/1.2.0.html` is written alongside. Only the second makes rollback
    possible — an install that moved the file into the archive, or wrote only the live
    copy, leaves the next version with nothing to revert to, and that is discovered at the
    moment reverting is urgent.
    """
    from agentevolver.extension import extension_manager

    previous = extension_manager.base_dir
    extension_manager.set_base_dir(str(tmp_path / "extensions"))
    source = HTML.replace('name="dynamic_audit"', 'name="saved_workflow"')
    try:
        active = Path(extension_manager.stage_path("workflow", "saved_workflow.html"))
        active.write_text(complete_html(source), encoding="utf-8")
        name = await extension_manager.add_component("workflow", str(active), run_smoke=False)
        definition = workflow_manager.get(name)
        archive = tmp_path / "extensions/.versions/workflow/saved_workflow/1.2.0.html"
        assert definition.status == WorkflowStatus.ACTIVE
        assert active.exists() and archive.exists()
    finally:
        workflow_manager.unregister("saved_workflow")
        extension_manager.set_base_dir(previous)


def test_the_registration_hook_finds_the_artifact_even_when_its_path_has_spaces(tmp_path):
    """The hook reads an agent's output, and an agent announces a file however it likes.

    Two shapes are accepted: a structured path the agent passed, and a path it only
    mentioned inside backticks in prose. Both directories and filenames here contain
    spaces, which is what breaks a naive whitespace split — the workflow would be written,
    reported as created, and never registered, leaving a file nothing loads.
    """
    from agentevolver.hook.default.workflow_registration import WorkflowRegistrationHook

    directory = tmp_path / "workflow files"
    directory.mkdir()
    artifact = directory / "review workflow.html"
    artifact.write_text("<workflow name='review'><flow><checkpoint /></flow></workflow>")
    assert WorkflowRegistrationHook._resolve(
        None, str(artifact), "", str(tmp_path),
    ) == str(artifact)
    assert WorkflowRegistrationHook._resolve(
        None, None, f"created `{artifact}`", str(tmp_path),
    ) == str(artifact)


class TestWorkflowResponse:
    """A workflow's outcome comes back as the response every capability returns.

    `workflow_manager.run` still hands back a `WorkflowRun`, and should: that is a run
    *record* — state machine, frames, checkpoint path, token cost — and something that can
    be paused and resumed is not a return value.

    But a caller that only wants the outcome had to know all of it. The agent loop did:
    alone among its five dispatch branches it read `.successful`, raised on `.error`, and
    JSON-encoded `.output` itself. Serializing in the caller is how the environment branch
    got two different renderings of one action, one of which returned `None` and one of
    which hung the run.
    """

    @pytest.mark.asyncio
    async def test_a_successful_run_returns_text_the_model_can_read(self) -> None:
        from agentevolver.response.types import ResponseType
        from agentevolver.workflow import workflow_manager

        run = SimpleNamespace(successful=True, output={"answer": 4}, id="run-1",
                              state="SUCCEEDED", error=None)
        with patch.object(type(workflow_manager), "run", AsyncMock(return_value=run)):
            response = await workflow_manager("adder", input={})

        assert response.success
        assert response.type == ResponseType.WORKFLOW
        assert json.loads(response.message) == {"answer": 4}
        assert response.data["run_id"] == "run-1"

    @pytest.mark.asyncio
    async def test_string_output_is_not_json_encoded_again(self) -> None:
        """A workflow that already returned prose should not reach the model in quotes."""
        from agentevolver.workflow import workflow_manager

        run = SimpleNamespace(successful=True, output="done", id="r", state="SUCCEEDED", error=None)
        with patch.object(type(workflow_manager), "run", AsyncMock(return_value=run)):
            response = await workflow_manager("w", input={})

        assert response.message == "done"

    @pytest.mark.asyncio
    async def test_a_failed_run_is_reported_not_raised(self) -> None:
        """The caller decides what a failure means, exactly as it does for a tool.

        Raising from the manager would make a workflow the one capability whose failure
        arrives as an exception rather than as an unsuccessful response.
        """
        from agentevolver.workflow import workflow_manager

        run = SimpleNamespace(successful=False, output=None, id="r", state="FAILED",
                              error="step 2 timed out")
        with patch.object(type(workflow_manager), "run", AsyncMock(return_value=run)):
            response = await workflow_manager("w", input={})

        assert not response.success
        assert response.message == "step 2 timed out"
        assert response.data["state"] == "FAILED"

    @pytest.mark.asyncio
    async def test_the_run_record_is_still_reachable(self) -> None:
        """`data.run_id` is what keeps this from throwing away the record.

        A caller that wants the frames, the checkpoint, or the token cost still can —
        the response is a second face on the same execution, not a replacement.
        """
        from agentevolver.workflow import workflow_manager

        run = SimpleNamespace(successful=True, output=1, id="run-42", state="SUCCEEDED", error=None)
        with patch.object(type(workflow_manager), "run", AsyncMock(return_value=run)):
            response = await workflow_manager("w", input={})

        assert response.data["run_id"] == "run-42"
