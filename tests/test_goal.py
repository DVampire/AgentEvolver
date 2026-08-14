"""A goal the agent can rewrite is not a goal, it is a note.

The objective a run is judged against has to be harder to move than the run itself.
An agent that may edit it has a way out of every hard task: when the work stalls,
narrow the objective to whatever is already done, mark it complete, and the
trajectory reads as success while nothing was achieved. So the store — not the tool,
which the next caller can route around — refuses create, edit, pause and resume
without a direct human, and lets the agent report only where the goal stands.

These tests pin both halves. The refusals must hold when the model asks nicely, when
it puts an authority in its own arguments, and when it is a sub-agent whose parent
was talking to a human. The permissions must also hold: an agent that cannot report
completion has no way to end, and a store that refused everything would pass every
authority test here while being useless.
"""

from datetime import datetime, timezone

import pytest

from agentevolver.paths import P, path_manager
from agentevolver.task.goal import GoalStore, authority_of, goal_manager, session_of
from agentevolver.task.types import (
    GoalAction,
    GoalAuthority,
    GoalAuthorityError,
    GoalPhase,
    GoalRevisionError,
    GoalStateError,
)
from agentevolver.tool.default.goal import CreateGoalTool, GetGoalTool, UpdateGoalTool
from agentevolver.utils.singleton import Singleton

HUMAN = GoalAuthority.HUMAN
AGENT = GoalAuthority.AGENT


class _Ctx:
    """A context as a tool receives one. ``extra`` is the host's channel, not the model's."""

    def __init__(self, session_id: str, *, human: bool = False, parent: str = ""):
        self.id = session_id
        self.extra = {}
        if human:
            self.extra["human_turn"] = True
        if parent:
            self.extra["parent_session_id"] = parent


def _goal(session_id: str, objective: str = "Ship the migration"):
    return goal_manager.create(session_id=session_id, objective=objective, authority=HUMAN)


# --------------------------------------------------------------------------- #
# Who may change what
# --------------------------------------------------------------------------- #
def test_an_agent_cannot_set_its_own_goal():
    """The first refusal. Everything else is a variation on it."""
    with pytest.raises(GoalAuthorityError):
        goal_manager.create(session_id="g_create", objective="Whatever I decide", authority=AGENT)
    assert goal_manager.current("g_create") is None


def test_an_agent_cannot_rewrite_the_objective_it_is_measured_against():
    """The tempting reading is that an edit is harmless because the goal still exists.

    It is the opposite: an edit is the one change that can turn a failed run into a
    passed one without any work happening, because it moves the thing "done" is
    compared against.
    """
    goal = _goal("g_edit")
    with pytest.raises(GoalAuthorityError):
        goal_manager.update(session_id="g_edit", goal_id=goal.id, revision=goal.revision,
                            action=GoalAction.EDIT, authority=AGENT,
                            objective="Ship something smaller")
    assert goal_manager.current("g_edit").objective == "Ship the migration"


def test_an_agent_cannot_pause_a_goal_to_get_out_from_under_it():
    """Pausing is not reporting; it is deciding the objective stops applying."""
    goal = _goal("g_pause")
    with pytest.raises(GoalAuthorityError):
        goal_manager.update(session_id="g_pause", goal_id=goal.id, revision=goal.revision,
                            action=GoalAction.PAUSE, authority=AGENT)
    assert goal_manager.current("g_pause").phase is GoalPhase.ACTIVE


def test_the_agent_is_the_one_who_reports_completion():
    """The mirror of the refusals, and the reason they are not simply "agents cannot".

    Only the agent knows whether the objective was met. A store that also required a
    human here would pass every test above while making the goal impossible to close,
    which is the failure mode a blanket rule produces.
    """
    goal = _goal("g_complete")
    done = goal_manager.update(session_id="g_complete", goal_id=goal.id, revision=goal.revision,
                               action=GoalAction.COMPLETE, authority=AGENT)
    assert done.phase is GoalPhase.COMPLETE


def test_the_agent_may_report_blocked_but_must_name_the_condition():
    """"Blocked" without a condition is indistinguishable from "this is hard".

    Unchecked, it becomes the cheapest exit from any difficult goal, so the reason is
    required and is what a human reads to decide whether it is true.
    """
    goal = _goal("g_blocked")
    with pytest.raises(GoalStateError):
        goal_manager.update(session_id="g_blocked", goal_id=goal.id, revision=goal.revision,
                            action=GoalAction.BLOCKED, authority=AGENT, blocked_reason="   ")

    blocked = goal_manager.update(session_id="g_blocked", goal_id=goal.id, revision=goal.revision,
                                  action=GoalAction.BLOCKED, authority=AGENT,
                                  blocked_reason="The staging database has been unreachable since 14:02.")
    assert blocked.phase is GoalPhase.BLOCKED
    assert "14:02" in blocked.blocked_reason


def test_a_human_can_still_move_what_the_agent_reported():
    """A blocked goal is a claim, not a verdict; the human decides what happens to it."""
    goal = _goal("g_override")
    blocked = goal_manager.update(session_id="g_override", goal_id=goal.id, revision=goal.revision,
                                  action=GoalAction.BLOCKED, authority=AGENT,
                                  blocked_reason="No credentials for the deploy target.")
    resumed = goal_manager.update(session_id="g_override", goal_id=goal.id, revision=blocked.revision,
                                  action=GoalAction.RESUME, authority=HUMAN)
    assert resumed.phase is GoalPhase.ACTIVE
    assert resumed.blocked_reason is None, "a resumed goal still carrying its blocker reads as blocked"


# --------------------------------------------------------------------------- #
# Where authority comes from
# --------------------------------------------------------------------------- #
def test_authority_comes_from_the_host_stamp_and_not_from_the_run_being_a_run():
    """An unstamped run is an agent, however it was started.

    The tempting default is the other way round — treat anything not obviously a
    sub-agent as human — which hands human authority to every autonomous evolver run
    in the system.
    """
    assert authority_of(_Ctx("s")) is AGENT
    assert authority_of(_Ctx("s", human=True)) is HUMAN


def test_a_subagent_does_not_inherit_the_authority_of_the_turn_that_spawned_it():
    """A sub-agent's context is seeded from its parent's, which is the trap.

    The parent may well have been serving a human. The sub-agent is still the system
    talking to itself, and if the stamp travelled down the graph, "ask a sub-agent to
    do it" would be a one-step escape from every rule above.
    """
    assert authority_of(_Ctx("child", human=True, parent="parent-session")) is AGENT


def test_a_goal_belongs_to_the_project_not_to_one_line_of_dialogue():
    """``ctx.id`` follows the conversation; a project can hold several of them.

    Keyed on ctx.id, a second conversation in the same project would see no goal and
    be free to create a competing one.
    """
    ctx = _Ctx("conversation-2")
    ctx.extra["project_id"] = "project-1"
    assert session_of(ctx) == "project-1"


# --------------------------------------------------------------------------- #
# Compare-and-set, and the file
# --------------------------------------------------------------------------- #
def test_a_change_written_against_a_stale_revision_is_refused():
    """Last-write-wins would silently discard the change the caller never saw.

    For a goal that change is usually the news — someone paused it, or restated the
    objective — so overwriting it is worse than failing.
    """
    goal = _goal("g_cas")
    goal_manager.update(session_id="g_cas", goal_id=goal.id, revision=goal.revision,
                        action=GoalAction.PAUSE, authority=HUMAN)
    with pytest.raises(GoalRevisionError):
        goal_manager.update(session_id="g_cas", goal_id=goal.id, revision=goal.revision,
                            action=GoalAction.COMPLETE, authority=AGENT)


def test_every_accepted_change_advances_the_revision():
    """Without this the compare-and-set token is decoration and stale writes land."""
    goal = _goal("g_rev")
    paused = goal_manager.update(session_id="g_rev", goal_id=goal.id, revision=goal.revision,
                                 action=GoalAction.PAUSE, authority=HUMAN)
    resumed = goal_manager.update(session_id="g_rev", goal_id=goal.id, revision=paused.revision,
                                  action=GoalAction.RESUME, authority=HUMAN)
    assert [goal.revision, paused.revision, resumed.revision] == [1, 2, 3]


def test_a_completed_goal_is_history_rather_than_something_to_reopen():
    """Reopening would make "complete" reversible, and a reversible claim is not one."""
    goal = _goal("g_closed")
    goal_manager.update(session_id="g_closed", goal_id=goal.id, revision=goal.revision,
                        action=GoalAction.COMPLETE, authority=AGENT)
    with pytest.raises(GoalStateError):
        goal_manager.update(session_id="g_closed", goal_id=goal.id, revision=2,
                            action=GoalAction.RESUME, authority=HUMAN)


def test_a_second_open_goal_is_refused_and_names_the_one_in_the_way():
    """Two live goals are two answers to "what is this session for".

    The agent would then be free to work toward whichever it was closer to reaching.
    """
    first = _goal("g_two", "Finish the migration")
    with pytest.raises(GoalStateError) as refusal:
        goal_manager.create(session_id="g_two", objective="Do something else", authority=HUMAN)
    assert first.id in str(refusal.value), "a refusal that does not say what is in the way cannot be acted on"


def test_a_goal_outlives_the_process_that_set_it():
    """The point of persisting it. A goal that died with the run would be a variable.

    The store is a singleton, so the restart is simulated by dropping the instance and
    building another; state that only lived in memory would come back empty.
    """
    goal = _goal("g_restart", "Keep the nightly ETL green")
    path = path_manager.get(P.SESSION_GOALS, owner="local", session_id="g_restart")
    assert path.is_file(), f"the goal was never written to {path}"

    Singleton._instances.pop(GoalStore, None)
    try:
        revived = GoalStore()
        assert revived.current("g_restart").id == goal.id
        assert revived.current("g_restart").objective == "Keep the nightly ETL green"
    finally:
        Singleton._instances[GoalStore] = goal_manager


def test_goals_are_scoped_to_their_session():
    """One project's objective must not appear in another's, or be closed from it."""
    a = _goal("g_scope_a", "Objective A")
    b = _goal("g_scope_b", "Objective B")
    assert goal_manager.current("g_scope_a").id == a.id
    assert goal_manager.current("g_scope_b").id == b.id
    assert goal_manager.current("g_scope_a").objective == "Objective A"


def test_timestamps_come_from_the_injected_clock(monkeypatch):
    """Pinned rather than waited for: a test that sleeps to prove a timestamp flakes."""
    pinned = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(goal_manager, "clock", lambda: pinned)
    goal = _goal("g_clock")
    assert goal.created_at == pinned
    assert goal.updated_at == pinned


# --------------------------------------------------------------------------- #
# The tools
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_no_goal_is_an_answer_rather_than_an_empty_result():
    """An empty message reads as a broken tool, and the model retries instead of asking."""
    result = await GetGoalTool()(ctx=_Ctx("t_empty"))
    assert result.success
    assert "No goal" in result.message
    assert result.data["goal"] is None


@pytest.mark.asyncio
async def test_the_read_hands_back_exactly_what_an_update_has_to_quote():
    """Read-before-write only works if the read makes the two values impossible to miss."""
    await CreateGoalTool()(objective="Ship the migration", ctx=_Ctx("t_read", human=True))
    result = await GetGoalTool()(ctx=_Ctx("t_read"))
    assert result.data["goal"]["revision"] == 1
    assert result.data["goal"]["goal_id"] in result.message
    assert "revision=1" in result.message


@pytest.mark.asyncio
async def test_the_tool_refuses_an_agents_create_and_says_why():
    """A refusal the model cannot read is a refusal it retries verbatim."""
    result = await CreateGoalTool()(objective="A goal I set myself", ctx=_Ctx("t_refuse"))
    assert not result.success
    assert "human" in result.message.lower()


@pytest.mark.asyncio
async def test_authority_written_into_the_arguments_buys_nothing():
    """The one attack the whole design exists to stop.

    Tool arguments are the model's own output; if any of them could name an authority,
    the model would have that authority. The kwarg below is accepted by the signature
    (every tool takes **kwargs) and must simply have no effect.
    """
    created = await CreateGoalTool()(objective="Self-granted", ctx=_Ctx("t_claim"),
                                     authority="human", human_turn=True)
    assert not created.success

    goal = _goal("t_claim2")
    result = await UpdateGoalTool()(goal_id=goal.id, revision=goal.revision, action="edit",
                                    objective="Something easier", ctx=_Ctx("t_claim2"),
                                    authority="human")
    assert not result.success
    assert goal_manager.current("t_claim2").objective == "Ship the migration"


@pytest.mark.asyncio
async def test_an_unknown_action_lists_the_ones_that_exist():
    """A bare rejection leaves the model guessing at a vocabulary it cannot see."""
    goal = _goal("t_action")
    result = await UpdateGoalTool()(goal_id=goal.id, revision=goal.revision,
                                    action="finish", ctx=_Ctx("t_action"))
    assert not result.success
    assert "complete" in result.message


@pytest.mark.asyncio
async def test_a_human_turn_through_the_tools_can_do_the_whole_lifecycle():
    """End to end, so the refusals above cannot be passing because everything fails."""
    ctx = _Ctx("t_life", human=True)
    created = await CreateGoalTool()(objective="Finish the audit", ctx=ctx)
    assert created.success
    goal_id = created.data["goal"]["goal_id"]

    paused = await UpdateGoalTool()(goal_id=goal_id, revision=1, action="pause", ctx=ctx)
    resumed = await UpdateGoalTool()(goal_id=goal_id, revision=2, action="resume", ctx=ctx)
    edited = await UpdateGoalTool()(goal_id=goal_id, revision=3, action="edit",
                                    objective="Finish the audit and file it", ctx=ctx)
    assert [paused.data["goal"]["phase"], resumed.data["goal"]["phase"]] == ["paused", "active"]
    assert edited.data["goal"]["objective"] == "Finish the audit and file it"

    # The agent, with no stamp at all, still closes it.
    done = await UpdateGoalTool()(goal_id=goal_id, revision=4, action="complete",
                                  ctx=_Ctx("t_life"))
    assert done.success and done.data["goal"]["phase"] == "complete"
