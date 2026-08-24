"""Plan mode holds a run to reading until a person approves what it intends to do.

A gate is only worth having if it is closed for the right things, and the cheap way
to decide that — the tool's name — is the one this repo has already paid for once:
`hook/default/repeat_tool.py` documents a predecessor that classified tools by
substring, mislabelled honest ones, and blocked on the mislabel. So the rule here
reads declarations, and the failures worth pinning are the ones that would quietly
reopen the gate: treating an undeclared capability as safe (`bash_tool` declares
nothing, and it can do anything), letting a sub-agent dispatch through (its effects
are whatever the child does), or leaving the agent with no legal move at all, which
spends a whole budget discovering that nothing is allowed.

The other half is consent. `exit_plan_mode` must lift the gate only on an explicit
approval — a decline, a timeout, or a review that never opened must all leave it
closed — because an agent that reads a rejection as a success goes on to carry out
the plan that was just refused.
"""

import asyncio
import contextlib
import io
from pathlib import Path

import pytest

from agentevolver.conversation.question import question_manager
from agentevolver.hook.default.plan_mode import PlanModeHook
from agentevolver.hook.types import HookContext, HookDecision, HookEvent
from agentevolver.plan.server import (
    ALWAYS_ALLOWED,
    PlanManagerServer,
    action_is_allowed,
)
from agentevolver.tool.default.exit_plan_mode import (
    APPROVE_LABEL,
    DECLINE_LABEL,
    ExitPlanModeTool,
)


@pytest.fixture
def plans(monkeypatch):
    """A manager built without ``__init__``, wired in wherever plan state is read.

    ``PlanManagerServer`` is a singleton, so constructing it normally hands back the
    process-wide instance and leaks an active gate into every later test.
    """
    manager = PlanManagerServer.__new__(PlanManagerServer)
    manager._states = {}
    monkeypatch.setattr("agentevolver.plan.server.plan_manager", manager)
    return manager


class Ctx:
    """A stand-in for the AgentContext a tool is handed."""

    def __init__(self, id="run-1", name="code_agent", extra=None):
        self.id = id
        self.name = name
        self.extra = extra or {}


def hook_context(name, kind="tool", session_id="run-1"):
    """One PRE_ACTION payload, shaped as ``Agent._dispatch_one`` builds it."""
    return HookContext(
        id=session_id,
        name="plan_mode_hook",
        input={"event": HookEvent.PRE_ACTION, "agent_name": "code_agent",
               "action": {"index": 0, "type": kind, "name": name, "args": "{}"}},
    )


async def decide(hook, monkeypatch, name, *, kind="tool", declaration=None,
                 session_id="run-1"):
    """Run the gate with a fixed declaration for the capability under test.

    The real lookup goes through four capability managers that a unit test has not
    initialized, so it would return ``None`` for everything and the gate would look
    correct while testing only its fallback.
    """
    async def fixed(_kind, _name):
        return declaration

    monkeypatch.setattr("agentevolver.plan.server.declaration_of", fixed)
    return await hook.handle(hook_context(name, kind=kind, session_id=session_id))


async def answer_review(verdict, custom="", session_id="run-1"):
    """Answer the plan review the way a UI would, once it appears."""
    for _ in range(200):
        pending = question_manager.pending(session_id)
        if pending:
            question_manager.answer(pending[0].id,
                                    [{"id": "plan-review", "selected": [verdict],
                                      "custom": custom}])
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the plan review never opened")


# --------------------------------------------------------------------------- #
# What the gate lets through
# --------------------------------------------------------------------------- #
def test_a_capability_that_declared_no_effects_runs_in_plan_mode():
    """Exploration is the work plan mode is *for*.

    `read_file_tool`, `grep_search_tool` and the rest declare ``mutates: False``. A
    gate that blocked them would leave the agent unable to learn anything before
    proposing a plan about it.
    """
    assert action_is_allowed("tool", "read_file_tool", {"mutates": False}) is True


def test_a_read_only_capability_runs_even_without_a_mutates_declaration():
    """Two fields say the same thing, and neither is universal.

    ``escalate_tool`` declares ``permission_mode: read_only`` and no ``mutates``;
    the search tools declare the reverse. Honouring only one would block half the
    capabilities that have already said they are safe.
    """
    assert action_is_allowed("tool", "escalate_tool",
                             {"mutates": None, "permission_mode": "read_only"}) is True


def test_a_capability_that_declared_nothing_is_refused():
    """Silence is not a claim of safety, and `bash_tool` is the silent one.

    This is the tempting shortcut: most tools declare nothing, so blocking them all
    feels overzealous. But the undeclared set is exactly where `bash_tool` and
    `code_interpreter_tool` sit — the two capabilities that can do anything at all —
    and reading "nothing declared" as "safe" hands them the gate that exists to hold
    them.
    """
    assert action_is_allowed("tool", "bash_tool", None) is False
    assert action_is_allowed("tool", "bash_tool", {"mutates": None,
                                                   "permission_mode": "workspace_write"}) is False


def test_a_capability_that_declared_it_mutates_is_refused():
    """The straightforward case, and the reason the gate exists."""
    assert action_is_allowed("tool", "write_file_tool", {"mutates": True}) is False


def test_dispatching_a_sub_agent_is_refused_however_it_is_declared():
    """A child agent's effects are whatever it does, which no declaration covers.

    Tempting to let a delegation through on the parent's declaration — the dispatch
    itself writes nothing. But the child runs its own loop under its own gate state,
    so approving the dispatch approves everything the child then chooses to do.
    """
    assert action_is_allowed("agent", "code_agent", {"mutates": False}) is False
    assert action_is_allowed("workflow", "build_site", {"permission_mode": "read_only"}) is False


def test_the_agent_always_has_a_legal_move():
    """Proposing, asking, and stopping are never blocked.

    A gate with no way through produces an agent that retries until its budget is
    gone — the failure looks like a loop and is actually a deadlock. Each of these
    three is either the way out of plan mode, the way to talk to the person holding
    it, or the way to end the run.
    """
    assert ALWAYS_ALLOWED == {"exit_plan_mode", "ask_user_question", "done_tool"}
    for name in ALWAYS_ALLOWED:
        # No declaration at all: the exemption must not depend on one.
        assert action_is_allowed("tool", name, None) is True


# --------------------------------------------------------------------------- #
# The hook
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_gate_is_open_when_no_one_put_the_run_in_plan_mode(plans, monkeypatch):
    """The hook is registered for every run, so its resting state must be ALLOW.

    Getting this wrong would block every mutating action in the framework the moment
    the hook was registered, with no one having asked for plan mode at all.
    """
    result = await decide(PlanModeHook(), monkeypatch, "bash_tool")
    assert result.decision == HookDecision.ALLOW


@pytest.mark.asyncio
async def test_a_refused_action_is_told_how_to_stop_being_refused(plans, monkeypatch):
    """The reason reaches the model, and it names `exit_plan_mode`.

    A bare "blocked" produces the same call again next turn: the model has no way to
    know a gate exists, let alone which tool opens it. `Agent._dispatch_one` returns
    this reason as the action's ``error``, so it is the model's only view of why
    nothing happened.
    """
    plans.enter("run-1")
    result = await decide(PlanModeHook(), monkeypatch, "bash_tool")

    assert result.decision == HookDecision.BLOCK
    assert "bash_tool" in result.reason
    assert "exit_plan_mode" in result.reason


@pytest.mark.asyncio
async def test_one_runs_plan_mode_does_not_gate_another_run(plans, monkeypatch):
    """State is keyed by run, and the hook itself holds none.

    Two sessions can be in flight at once. A gate stored on the hook instance would
    let one person's plan mode stop a stranger's agent.
    """
    plans.enter("run-1")
    other = await decide(PlanModeHook(), monkeypatch, "bash_tool", session_id="run-2")
    assert other.decision == HookDecision.ALLOW


@pytest.mark.asyncio
async def test_the_gate_ignores_events_that_are_not_pre_action(plans, monkeypatch):
    """Hooks are dispatched by name and receive whatever event the caller sends."""
    plans.enter("run-1")
    ctx = hook_context("bash_tool")
    ctx.input["event"] = HookEvent.POST_ACTION
    assert (await PlanModeHook().handle(ctx)).decision == HookDecision.ALLOW


def test_the_agents_action_dispatch_consults_the_gate():
    """The hook is inert unless `Agent._dispatch_one` calls it by name.

    Hook dispatch is exact-name routing, not event subscription: a hook nobody names
    never runs. Registering `plan_mode_hook` and forgetting the call site would leave
    every test above green and the gate wide open in production.
    """
    from pathlib import Path

    source = Path(__file__).parents[1] / "agentevolver" / "agent" / "types.py"
    text = source.read_text(encoding="utf-8")
    assert 'name="plan_mode_hook"' in text
    # And its reason must be handed back as the action's error, not swallowed.
    assert '"error": plan_gate.reason' in text


# --------------------------------------------------------------------------- #
# Leaving plan mode
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_approved_plan_opens_the_gate_and_is_kept_verbatim(plans):
    """Approval records what was agreed, not just that something was.

    A bare boolean leaves nothing able to say what the person consented to — which
    is the one fact worth having when the work afterwards goes somewhere else.
    """
    plans.enter("run-1")
    asyncio.create_task(answer_review(APPROVE_LABEL))

    response = await ExitPlanModeTool()(plan="1. Edit the retry branch.", ctx=Ctx())

    assert response.success
    assert plans.active("run-1") is False
    assert plans.state("run-1").approved_plan == "1. Edit the retry branch."


@pytest.mark.asyncio
async def test_a_declined_plan_is_a_failed_call_and_the_gate_stays_shut(plans):
    """A refusal reported through a *successful* call reads as progress.

    The model sees the call succeeded and moves on to carry out the plan it was just
    told not to. The verdict has to be in the success flag, not only in the prose.
    """
    plans.enter("run-1")
    asyncio.create_task(answer_review(DECLINE_LABEL, custom="use the existing retry helper"))

    response = await ExitPlanModeTool()(plan="1. Write a new retry helper.", ctx=Ctx())

    assert response.success is False
    assert "use the existing retry helper" in response.message
    assert plans.active("run-1") is True


@pytest.mark.asyncio
async def test_nobody_reviewing_the_plan_leaves_the_gate_shut(plans, monkeypatch):
    """A timeout is not consent.

    The dangerous reading is "the person did not object", which would make walking
    away from the screen equivalent to approving. Silence has to leave plan mode on.
    """
    monkeypatch.setattr("agentevolver.conversation.question.DEFAULT_QUESTION_TIMEOUT_S", 0.01)
    plans.enter("run-1")

    response = await ExitPlanModeTool()(plan="1. Do the thing.", ctx=Ctx())

    assert response.success is False
    assert plans.active("run-1") is True
    assert plans.state("run-1").approved_plan == ""


@pytest.mark.asyncio
async def test_exiting_plan_mode_when_not_in_it_changes_nothing(plans):
    """No review is opened, so nobody is asked to approve a plan for no reason."""
    response = await ExitPlanModeTool()(plan="1. Do the thing.", ctx=Ctx())

    assert response.success is False
    assert "not in plan mode" in response.message
    assert question_manager.pending("run-1") == []


@pytest.mark.asyncio
async def test_an_empty_plan_is_refused_before_anyone_is_asked(plans):
    """An empty review shows the person a decision with nothing to decide about."""
    plans.enter("run-1")
    response = await ExitPlanModeTool()(plan="   ", ctx=Ctx())

    assert response.success is False
    assert question_manager.pending("run-1") == []
    assert plans.active("run-1") is True


@pytest.mark.asyncio
async def test_the_review_declares_the_label_that_approves_it(plans):
    """A `plan-review` intent names its approve label rather than relying on order.

    Two UIs render this differently — one as a plan review, one as a plain menu —
    and both must agree on which answer means yes. Inferring it from option position
    makes reordering the menu silently invert the verdict.
    """
    plans.enter("run-1")
    asking = asyncio.create_task(ExitPlanModeTool()(plan="the plan", ctx=Ctx()))

    for _ in range(200):
        pending = question_manager.pending("run-1")
        if pending:
            break
        await asyncio.sleep(0.005)
    [record] = pending
    [question] = record.questions

    assert question.intent.type == "plan-review"
    assert question.intent.approve == APPROVE_LABEL
    assert question.detail == "the plan"  # the plan is the detail, not an option
    assert [option.label for option in question.options] == [APPROVE_LABEL, DECLINE_LABEL]

    question_manager.answer(record.id, [{"id": "plan-review", "selected": [DECLINE_LABEL]}])
    await asking


# --------------------------------------------------------------------------- #
# The state itself
# --------------------------------------------------------------------------- #
def test_re_entering_plan_mode_drops_the_previous_approval(plans):
    """Consent is given for one plan, not for a session.

    Carrying the last approved plan forward would let a second round of work inherit
    agreement that was given for the first — and the record would then claim a person
    approved something they never saw.
    """
    plans.enter("run-1")
    plans.approve("run-1", "the first plan")
    plans.enter("run-1")

    assert plans.active("run-1") is True
    assert plans.state("run-1").approved_plan == ""


def test_calling_plan_mode_off_is_not_the_same_as_approving_a_plan(plans):
    """`leave()` opens the gate and records no approval.

    Both end with ``active: False``, which is why collapsing them is tempting. But a
    cancelled plan mode must never read downstream as an agreed plan.
    """
    plans.enter("run-1")
    plans.leave("run-1")

    assert plans.active("run-1") is False
    assert plans.state("run-1").approved_plan == ""


def test_a_run_nobody_mentioned_is_not_in_plan_mode(plans):
    """Absence of a record is the inactive state, not a missing one."""
    assert plans.active("never-seen") is False
    assert plans.state("never-seen").active is False


def test_the_gateway_exposes_commands_to_read_and_set_plan_mode():
    """Gateway dispatch is by method name, so a missing handler fails only at runtime.

    Plan mode is set by a person, and the Gateway is where a person reaches the run.
    Without these there is no way to enter it at all.
    """
    from agentevolver.gateway.service import AgentGateway

    for method in ("plan.get", "plan.set"):
        assert getattr(AgentGateway, f"_command_{method.replace('.', '_')}", None) is not None


# --------------------------------------------------------------------------- #
# A gate that moves must say so
# --------------------------------------------------------------------------- #
def test_every_transition_announces_itself():
    """Only the gateway's own `plan.set` used to publish.

    So a plan the *agent* got approved through `exit_plan_mode` opened the gate in
    silence, and the UI went on saying plan mode was active — at the exact moment the
    person had just approved it. A state that changes without saying so is a UI that
    lies, and the durable fix is to make the transition responsible rather than each
    caller responsible for remembering.
    """
    from agentevolver.plan import plan_manager

    seen = []
    listener = lambda state: seen.append((state.session_id, state.active))
    plan_manager.subscribe(listener)
    try:
        plan_manager.enter("announce-1")
        plan_manager.approve("announce-1", "the plan")
        plan_manager.leave("announce-1")
    finally:
        plan_manager.unsubscribe(listener)
        plan_manager.forget("announce-1")

    assert [active for _, active in seen] == [True, False, False], (
        "a transition did not announce; whichever one is missing is a state the UI "
        "cannot follow")


def test_a_failing_listener_does_not_undo_the_transition():
    """The gate has already moved by the time listeners run.

    Refusing to finish because a subscriber raised would leave the caller believing the
    gate had not moved while it had — the worst of both, and unrecoverable from inside
    the caller.
    """
    from agentevolver.plan import plan_manager

    def explode(_state):
        raise RuntimeError("subscriber is broken")

    plan_manager.subscribe(explode)
    try:
        state = plan_manager.enter("announce-2")
    finally:
        plan_manager.unsubscribe(explode)
        plan_manager.forget("announce-2")

    assert state.active is True


def test_the_gateway_follows_the_manager_rather_than_its_own_command():
    """Publishing from `plan.set` alone is what created the gap.

    Reading it from the source: the subscription is the fact under test, and standing up
    a gateway to observe it would test the harness.
    """
    import inspect

    import agentevolver.gateway.service as service

    source = inspect.getsource(service)
    assert "plan_manager.subscribe" in source, (
        "the gateway no longer follows plan state, so an agent-approved plan reaches no UI")
    assert source.count('_publish("plan.mode.changed"') == 1, (
        "plan.mode.changed is published from more than one place; the UI would see the "
        "same transition twice")


# --------------------------------------------------------------------------- #
# The declaration the gate reads has to be the one the tool wrote
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_built_in_keeps_the_declaration_it_wrote_after_registration():
    """Every test above hands `action_is_allowed` a dictionary built by hand.

    That checks the gate's arithmetic and nothing about its input, so the two halves
    could disagree indefinitely — and they did.
    `test_a_read_only_capability_runs_even_without_a_mutates_declaration` passes
    `{"mutates": None, "permission_mode": "read_only"}` and names `escalate_tool` as
    the tool that declares it. `escalate_tool` does declare exactly that. The registry
    was dropping the second field on the way in: `_load_from_registry` carried
    `mutates` and `call_timeout_seconds` off the class and not `permission_mode`, so
    `get_info` reported the `ToolConfig` fallback, `workspace_write`.

    The effect was that plan mode refused `escalate_tool` and `reply_tool` — the two
    ways an agent has of reaching a person while the gate is shut — and the suite
    stayed green, because the unit test asserted on `EscalateTool()` and the gate read
    the registry.

    So this one goes through the manager. `permission_mode` is asserted rather than
    the verdict, because a verdict can come out right for the wrong reason: the gate
    would also pass this tool if someone added it to `ALWAYS_ALLOWED`.
    """
    import agentevolver.tool.default  # noqa: F401 — importing is what registers them
    from agentevolver.tool import tool_manager

    with contextlib.redirect_stdout(io.StringIO()):
        await tool_manager.initialize(tool_names=["escalate_tool", "reply_tool",
                                                  "read_file_tool", "bash_tool"])

    for name, declared in (("escalate_tool", "read_only"), ("reply_tool", "read_only"),
                           ("read_file_tool", "read_only"), ("bash_tool", "workspace_write")):
        info = await tool_manager.get_info(name)
        assert info is not None, f"{name} did not register"
        assert info.permission_mode == declared, (
            f"{name} declares permission_mode={declared!r} on its class, and the registry "
            f"reports {info.permission_mode!r}"
        )


@pytest.mark.asyncio
async def test_the_gate_reads_a_real_declaration_and_lets_the_agent_speak():
    """End to end, with nothing hand-built: register, read, decide.

    Talking to a person is the one thing that must work while the gate is shut, and
    two of the three ways of doing it were reaching the gate as `workspace_write`.
    """
    import agentevolver.tool.default  # noqa: F401
    from agentevolver.plan import declaration_of
    from agentevolver.tool import tool_manager

    with contextlib.redirect_stdout(io.StringIO()):
        await tool_manager.initialize(
            tool_names=["escalate_tool", "reply_tool", "report_tool", "bash_tool",
                        "write_file_tool"])

    for name in ("escalate_tool", "reply_tool", "report_tool"):
        assert action_is_allowed("tool", name, await declaration_of("tool", name)) is True, (
            f"{name} is how the agent reaches a person; plan mode must not block it"
        )
    for name in ("bash_tool", "write_file_tool"):
        assert action_is_allowed("tool", name, await declaration_of("tool", name)) is False, (
            f"{name} can change things and must stay behind the gate"
        )


# --------------------------------------------------------------------------- #
# In auto mode the agent writes plan.md itself, so it has to be allowed to
# --------------------------------------------------------------------------- #
def test_the_agent_is_allowed_to_write_its_own_plan():
    """`auto` asks the agent for a document. It must be somewhere the agent may write.

    `plan.md` first went beside `workspace/`, with the framework's other session files,
    and `workspace_write` refused it: "outside the writable roots". Nothing raised —
    `auto` would simply have been a mode that asks every run for a plan the agent is not
    permitted to create, and the refusal would have read as the agent's mistake.

    Checked through `permission_manager` rather than by comparing strings, because the
    permission system is what actually decides, and a path can look contained while the
    boundary resolves differently.
    """
    from agentevolver.paths import P, path_manager
    from agentevolver.permission import permission_manager
    from agentevolver.permission.types import Operation, PermissionMode, PermissionRequest

    workspace = path_manager.get(P.SESSION_WORKSPACE, owner="local", session_id="plan_probe")
    plan = path_manager.get(P.SESSION_PLAN, owner="local", session_id="plan_probe")

    permission_manager.register(entity_name="plan_probe_tool",
                                mode=PermissionMode.WORKSPACE_WRITE, workspace=str(workspace))
    try:
        verdict = permission_manager.check(
            "plan_probe_tool", PermissionRequest(op=Operation.WRITE, target=str(plan)))
        assert verdict.allowed, (
            f"a workspace_write tool cannot write {plan}: {verdict.reason}. In auto mode "
            f"the agent maintains this file with write_file_tool."
        )
    finally:
        permission_manager.unregister("plan_probe_tool")


def test_auto_is_the_default_stance():
    """A run nobody configured plans adaptively rather than not at all."""
    from agentevolver.plan import PlanMode, plan_manager

    assert plan_manager.mode("a-run-nobody-set") is PlanMode.AUTO
    assert plan_manager.active("a-run-nobody-set") is False, (
        "auto must not gate: the agent decides whether to plan, and nothing is refused"
    )


def test_an_approved_plan_lands_on_disk_because_the_gate_forbids_writing_it():
    """Under `plan` the agent cannot write `plan.md` — `write_file_tool` is refused.

    So the approval is the only moment the document can be created. A review that left
    no file would mean the plan a person agreed to was unreadable one step later, while
    every *auto* run had one.
    """
    from agentevolver.plan import plan_manager, read_plan, write_plan

    session = "plan_write_probe"
    from agentevolver.paths import path_manager

    path_manager.bind_session("local", session)
    plan_manager.enter(session)
    assert plan_manager.active(session) is True
    plan_manager.approve(session, "## Goal\nship it\n")
    try:
        assert read_plan() == "## Goal\nship it\n"
        assert plan_manager.active(session) is False
    finally:
        write_plan("")
        plan_manager.forget(session)
        path_manager.unbind_session()


@pytest.mark.asyncio
async def test_in_auto_the_empty_plan_slot_still_says_what_to_do(tmp_path):
    """`{% if plan %}` renders nothing for an empty string.

    So in `auto` — the default, and therefore most runs — an agent that has not written
    `plan.md` yet would see no plan block at all, and never be told the document is its
    to write. The obligation has to ride in the slot, not beside it.

    Not in `<errors>`, which is where this notice started: that block means "the previous
    step failed", and the agent has learned it as a signal something went wrong. A
    standing instruction placed there is read against its own grain.
    """
    from agentevolver.agent.actor.code_agent import CodeAgent
    from agentevolver.plan import AUTO_MODE_NOTICE, PlanMode, plan_manager

    class _Ctx:
        id = "auto_slot_probe"

    plan_manager.set_mode("auto_slot_probe", PlanMode.AUTO)
    try:
        slots = await CodeAgent(base_dir=str(tmp_path))._get_agent_context(task="t", ctx=_Ctx())     # noqa: SLF001
        assert slots["plan"], "the plan slot is empty, so the template renders no block"
        assert AUTO_MODE_NOTICE in slots["plan"]
    finally:
        plan_manager.forget("auto_slot_probe")


@pytest.mark.asyncio
async def test_a_written_plan_replaces_the_prompt_rather_than_joining_it(tmp_path):
    """Once there is a plan, the slot is the plan. The nudge would be noise beside it."""
    from agentevolver.agent.actor.code_agent import CodeAgent
    from agentevolver.plan import AUTO_MODE_NOTICE, PlanMode, plan_manager, write_plan

    class _Ctx:
        id = "auto_slot_written"

    from agentevolver.paths import path_manager

    # Bound, because that is what `read_plan()` resolves against — the slot shows the
    # run being rendered, not whatever session id happens to be passed around.
    path_manager.bind_session("local", "auto_slot_written")
    plan_manager.set_mode("auto_slot_written", PlanMode.AUTO)
    write_plan("## Goal\nmeasure the thing\n")
    try:
        slots = await CodeAgent(base_dir=str(tmp_path))._get_agent_context(task="t", ctx=_Ctx())     # noqa: SLF001
        assert "measure the thing" in slots["plan"]
        assert AUTO_MODE_NOTICE not in slots["plan"]
    finally:
        write_plan("")
        plan_manager.forget("auto_slot_written")
        path_manager.unbind_session()


def test_the_auto_notice_is_not_delivered_as_an_error():
    """The two notices reach the agent by different routes, deliberately.

    `plan` accompanies a refusal, so `<errors>` is where it belongs. `auto` refuses
    nothing, so it must not arrive there — this asserts the announcement path does not
    mention it at all, since that is the mistake that was made.
    """
    import inspect

    from agentevolver.agent.types import Agent

    source = inspect.getsource(Agent._announce_plan_mode)            # noqa: SLF001
    assert "PLAN_MODE_NOTICE" in source
    assert "AUTO_MODE_NOTICE" not in source, (
        "the auto notice is being delivered through action_errors, which renders as "
        "<errors> — a block that means the previous step failed"
    )


def test_the_layout_and_the_derived_plan_path_agree():
    """`P.SESSION_PLAN` is what a reader looks up; `plan_path()` is what runs.

    They are two statements of one location, and the layout entry spells out the
    `workspace` segment a second time. Composing in code is what makes an override
    move the plan with the workspace — but it also means the table could drift and
    nothing would notice, since nothing resolves that key any more. This is what
    notices.
    """
    from agentevolver.paths import P, path_manager
    from agentevolver.plan import plan_path

    path_manager.bind_session("local", "layout_agree")
    try:
        assert plan_path() == path_manager.get(P.SESSION_PLAN), (
            "the layout table and the derived path disagree about where plan.md lives")
    finally:
        path_manager.unbind_session()


def test_the_plan_follows_a_container_mount_override():
    """An override on the *workspace* has to move the plan, because the plan is in it.

    Resolving `P.SESSION_PLAN` directly did not: it is its own template, spelling out
    the workspace segment again, so overriding `SESSION_WORKSPACE` moved one and left
    the other. Inside a container that meant the agent was told about — and the approval
    wrote to — the host-layout path, which happens to be the same file through a second
    mount. Right by accident, and the same accident the sandbox boundary did not get.
    """
    from agentevolver.paths import P, path_manager
    from agentevolver.plan import plan_path

    path_manager.bind_session("local", "mount_plan")
    try:
        path_manager.override(P.SESSION_WORKSPACE, "/workspace")
        assert plan_path() == Path("/workspace/plan.md"), (
            f"the plan did not follow the workspace: {plan_path()}")
        # And the boundary agrees, so the agent may write what it is told about.
        from agentevolver.sandbox.project import check_session_path
        assert check_session_path(path=str(plan_path()), write=True) is None
    finally:
        path_manager.unbind_session()


def test_naming_this_run_is_not_a_way_to_lose_the_override():
    """`approve()` names the session it is approving, and that session is this one.

    Passing an id is how a caller asks about a *different* run, which is why an override
    does not answer such a call. But `plan_manager.approve(session_id, plan)` names the
    current one, so the rule is "the bound run, however it is spelled" rather than
    "only when nothing is passed".
    """
    from agentevolver.paths import P, path_manager
    from agentevolver.plan import plan_path

    path_manager.bind_session("local", "named_run")
    try:
        path_manager.override(P.SESSION_WORKSPACE, "/workspace")
        assert plan_path("named_run") == Path("/workspace/plan.md")
        assert plan_path(owner="local") == Path("/workspace/plan.md")
        # A genuinely different run is a different question, and gets the table's answer.
        assert plan_path("some_other_run") != Path("/workspace/plan.md")
    finally:
        path_manager.unbind_session()
