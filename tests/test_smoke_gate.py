"""The replay smoke gate reports a verdict on every path, including its own failures.

A newly evolved tool or agent imports cleanly long before it works. The gate catches the
rest by running a synthetic task through the real loop and treating any crash, error exit,
or timeout as a rejection — cheaply, before the component reaches the manifest.

What makes it delicate is where it sits. It runs *inside* the evolution path, so its own
failures are the dangerous ones: a probe that raises must become a rejection report, not
an exception, or a bug in the gate takes down the thing it was added to protect. The
signature promises this — "Returns a ReplayReport (never raises)" — and five call sites
believe it.

The verdict also has to be finer than a boolean. A timeout and an exception are both
failures but different diagnoses, and a run that reports success while having been stopped
by a constraint is not a passing run at all. The probe is injectable precisely so all of
this is decidable without a live model; until the coverage lane was introduced, nothing
used that seam.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from agentevolver.extension.smoke_gate import (
    EvolutionRejected,
    ReplayReport,
    _default_probe,
    _resolve_probe_model,
    replay_smoke,
)


def _probe_returning(report: ReplayReport):
    async def probe(module, name, model_role, timeout_s):
        return report
    return probe


# --------------------------------------------------------------------------- #
# The promise: a verdict, never an exception
# --------------------------------------------------------------------------- #
def test_a_probe_that_raises_becomes_a_rejection_rather_than_an_exception():
    """The gate's own failure must not be able to break the path it guards.

    This is the inversion that makes the whole thing safe to enable by default. A probe
    can fail for reasons that have nothing to do with the component under test — no
    model configured, a network blip — and if those propagated, adding the gate would
    have made evolution *less* reliable than not having it.
    """

    async def exploding(module, name, model_role, timeout_s):
        raise RuntimeError("the probe itself is broken")

    report = asyncio.run(replay_smoke("tool", "new_tool", probe=exploding))

    assert report.ok is False
    assert "probe error" in report.reason
    assert report.exit_reason == "error"
    # The component under test is still named, so the log line points somewhere useful.
    assert (report.module, report.name) == ("tool", "new_tool")


def test_a_failing_component_is_reported_not_raised():
    """Rejection is a return value because the *caller* owns the consequence.

    The module docstring is explicit that this only decides: rolling back to the previous
    archived version or unloading a brand-new one is the caller's job, and it needs the
    report to tell those two cases apart.
    """
    failed = ReplayReport(ok=False, reason="blew up on first use", exit_reason="error")

    report = asyncio.run(replay_smoke("agent", "bad", probe=_probe_returning(failed)))

    assert report.ok is False
    assert report.reason == "blew up on first use"


def test_a_passing_component_is_reported_as_passing():
    """The green path, so a gate that rejects everything cannot pass its own tests."""
    report = asyncio.run(
        replay_smoke("tool", "good", probe=_probe_returning(ReplayReport(ok=True)))
    )

    assert report.ok is True


def test_the_rejection_exception_exists_for_callers_that_want_to_stop():
    """`replay_smoke` never raises it; the caller does. Asserted so that a later change
    which starts raising from inside has to change this test and say why."""
    assert issubclass(EvolutionRejected, Exception)


# --------------------------------------------------------------------------- #
# Choosing the probe model
# --------------------------------------------------------------------------- #
def test_the_named_role_wins_over_the_generic_fallbacks():
    """Roles are tried in order, and the order is the point.

    A deployment that configured a cheap model specifically for smoke runs would be
    silently billed at main-model rates if the fallback were consulted first.
    """
    roles = {"smoke": "cheap-model", "main": "expensive-model", "custom": "chosen-model"}

    with patch("agentevolver.config.config") as config:
        config.model_roles = roles
        assert _resolve_probe_model("custom") == "chosen-model"


def test_an_unknown_role_falls_back_to_smoke_before_main():
    """`smoke` before `main` — the fallback still prefers the cheap option."""
    with patch("agentevolver.config.config") as config:
        config.model_roles = {"smoke": "cheap-model", "main": "expensive-model"}
        assert _resolve_probe_model("no-such-role") == "cheap-model"


def test_no_configured_roles_resolves_to_nothing_rather_than_guessing():
    """`None` means "leave the agent's own model alone", which is a real answer.

    Returning some default name instead would point the probe at a model that may not
    be configured, turning "no smoke model" into a probe failure and then into a
    rejection of a component that was never actually run.
    """
    with patch("agentevolver.config.config") as config:
        config.model_roles = None
        assert _resolve_probe_model("smoke") is None


def test_a_config_that_cannot_be_read_resolves_to_nothing():
    """Config access is wrapped because this runs in contexts where there is none.

    The stand-in has to be an *instance*: `__getattr__` on a class governs its instances,
    not the class object, so patching in the class itself makes `getattr` return the
    default and this test pass while never reaching the `except` it is named for. It was
    written that way first, and the coverage report is what showed the line still dark.
    """
    class Raising:
        def __getattr__(self, item):
            raise RuntimeError("no config in this process")

    with patch("agentevolver.config.config", new=Raising()):
        assert _resolve_probe_model("smoke") is None


def test_an_agent_that_will_not_take_the_probe_model_is_still_smoke_tested():
    """Narrowing the model is an optimisation; failing to do it must not skip the check.

    `model_copy` is a pydantic call that can reject an update — a component with an
    unusual agent shape, say. Letting that propagate would turn a cost optimisation into
    a rejection of a component that was never run.
    """
    class Stubborn(_Agent):
        def model_copy(self, update=None):
            raise TypeError("this agent will not be reconfigured")

    async def clean(*args, **kwargs):
        return _Response(success=True, data={})

    with patch("agentevolver.config.config") as config:
        config.model_roles = {"smoke": "cheap-model"}
        with patch("agentevolver.agent.server.agent_manager") as manager:
            async def get(_name):
                return Stubborn(clean)
            manager.get = get

            report = asyncio.run(_default_probe("tool", "t", "smoke", 5.0))

    assert report.ok is True


# --------------------------------------------------------------------------- #
# The default probe: what counts as a failure
# --------------------------------------------------------------------------- #
def test_a_probe_agent_that_will_not_load_is_a_failure_with_a_reason():
    """Naming the cause matters: this is not the component's fault.

    Without the reason in the report, a misconfigured harness looks identical to a
    genuinely broken component, and the fix is applied to the wrong thing.
    """
    with patch("agentevolver.agent.server.agent_manager") as manager:
        manager.get.side_effect = RuntimeError("registry is empty")
        report = asyncio.run(_default_probe("tool", "t", "smoke", 5.0))

    assert report.ok is False
    assert "probe agent unavailable" in report.reason


def test_a_missing_probe_agent_is_distinguished_from_one_that_failed_to_load():
    """`None` and "raised" are different harness faults and read differently in a log."""
    with patch("agentevolver.agent.server.agent_manager") as manager:
        async def absent(_name):
            return None
        manager.get = absent
        report = asyncio.run(_default_probe("tool", "t", "smoke", 5.0))

    assert report.ok is False
    assert "not registered" in report.reason


def test_a_smoke_run_that_never_finishes_is_a_timeout_not_an_error():
    """A runaway component and a crashing one need different diagnoses.

    This is the case the gate was most worth adding for — an evolved component that
    loops forever passes every import check and would otherwise hang the evolution run
    rather than fail it.
    """
    with patch("agentevolver.agent.server.agent_manager") as manager:
        async def hanging(*args, **kwargs):
            await asyncio.sleep(10)

        async def get(_name):
            agent = _Agent(hanging)
            return agent
        manager.get = get

        report = asyncio.run(_default_probe("tool", "t", "smoke", 0.05))

    assert report.ok is False
    assert report.exit_reason == "timeout"
    assert "timed out" in report.reason


def test_a_run_stopped_by_a_constraint_is_not_a_pass_even_when_it_reports_success():
    """The subtle one, and the reading a boolean check would get wrong.

    A constrained stop means the loop was cut short — by a step budget or a guard — so
    the component was never actually exercised. `success` alone is True there, and
    trusting it would wave through exactly the runaway components this gate exists for.
    """
    with patch("agentevolver.agent.server.agent_manager") as manager:
        async def stopped(*args, **kwargs):
            return _Response(success=True, data={"stopped_by_constraint": True})

        async def get(_name):
            return _Agent(stopped)
        manager.get = get

        report = asyncio.run(_default_probe("tool", "t", "smoke", 5.0))

    assert report.ok is False
    assert report.exit_reason == "error"


def test_a_clean_synthetic_run_passes():
    """The green path through the real probe, not just through an injected one."""
    with patch("agentevolver.agent.server.agent_manager") as manager:
        async def clean(*args, **kwargs):
            return _Response(success=True, data={})

        async def get(_name):
            return _Agent(clean)
        manager.get = get

        report = asyncio.run(_default_probe("tool", "t", "smoke", 5.0))

    assert report.ok is True
    assert report.exit_reason == "done"


def test_a_probe_run_that_raises_is_caught_inside_the_default_probe_too():
    """The outer guard in `replay_smoke` is not the only one; this path has its own,
    so the failure keeps its `exit_reason` instead of being flattened to "probe error"."""
    with patch("agentevolver.agent.server.agent_manager") as manager:
        async def blowing_up(*args, **kwargs):
            raise ValueError("bad schema")

        async def get(_name):
            return _Agent(blowing_up)
        manager.get = get

        report = asyncio.run(_default_probe("tool", "t", "smoke", 5.0))

    assert report.ok is False
    assert "smoke raised" in report.reason
    assert report.exit_reason == "error"


class _Agent:
    """A callable stand-in for a registered agent.

    Only the agent is replaced: `_default_probe` still builds the real `SessionContext`,
    resolves the probe model through the real config, and applies the real timeout. What
    a live agent would add is a model call, which is the boundary worth faking.
    """

    def __init__(self, behaviour):
        self._behaviour = behaviour

    def model_copy(self, update=None):
        return self

    async def __call__(self, *args, **kwargs):
        return await self._behaviour(*args, **kwargs)


class _Response:
    def __init__(self, success: bool, data: dict, message: str = ""):
        self.success = success
        self.data = data
        self.message = message
