"""The coverage gate goes red on a module nothing executes, and on a debt entry gone stale.

A gate whose rule is "some set must equal some other set" is the easiest kind to get
backwards and the hardest kind to notice is backwards, because both mistakes look like a
green run. Drop the second direction and the register never shrinks; compare against the
wrong side and every file becomes a violation at once, which reads as a broken gate and
gets deleted. Neither shows up as a failure of the thing being guarded, so the checker is
exercised here directly, against inputs it would otherwise only meet after an 80-second
measured run.

The register itself is checked too: an entry naming a file that no longer exists exempts
nothing, and would sit there looking like a considered decision.
"""

from pathlib import Path

import pytest

from tests.coverage_gate import FLOOR_PERCENT, NEVER_EXECUTED, violations

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: A run comfortably above the floor, so a test about dark files is never also a test
#: about the percentage.
_HEALTHY = FLOOR_PERCENT + 10.0


# --------------------------------------------------------------------------- #
# Files the run never reached
# --------------------------------------------------------------------------- #
def test_a_module_no_test_executes_fails_the_gate():
    """The case the gate exists for: code that is present, shipped, and never run.

    This is what a newly-added-but-never-wired module looks like from the outside — the
    same shape as `utils/text_compress.py`, which sat unreferenced through every green
    run until coverage was measured for the first time.
    """
    problems = violations({"agentevolver/brand_new.py": 0}, _HEALTHY, registered={})

    assert len(problems) == 1
    # The message has to name the file and the two ways out; a bare "coverage failed"
    # sends the reader to the wrong place.
    assert "agentevolver/brand_new.py" in problems[0]
    assert "deleted" in problems[0] and "NEVER_EXECUTED" in problems[0]


def test_a_registered_file_may_stay_dark():
    """Registering a file is what makes the run green — otherwise nobody would register.

    Tempting to assert only the failing direction, which would still pass if the register
    were ignored entirely and every dark file failed forever.
    """
    registered = {"agentevolver/docker/server.py": "scaffold — not yet implemented"}

    assert violations({"agentevolver/docker/server.py": 0}, _HEALTHY, registered) == []


def test_a_covered_file_is_not_reported_dark():
    """One executed line is enough; the gate grades reachability, not thoroughness.

    The floor is what watches the percentage. If this check also demanded a minimum per
    file, every partially-tested module in the repo would fail at once.
    """
    assert violations({"agentevolver/job/server.py": 1}, _HEALTHY, registered={}) == []


# --------------------------------------------------------------------------- #
# The ratchet: debt that has been paid must leave the register
# --------------------------------------------------------------------------- #
def test_a_registered_file_that_became_covered_fails_the_gate():
    """The direction that is easy to leave out, and the one that keeps the list honest.

    Without it the register only ever grows. Worse, a file whose tests were written
    stays permanently exempt from the check it now passes — so if its tests are later
    deleted, it goes dark again and the stale entry waves it through.
    """
    registered = {"agentevolver/gateway/transport.py": "needs a live gateway process"}

    problems = violations({"agentevolver/gateway/transport.py": 42}, _HEALTHY, registered)

    assert len(problems) == 1
    assert "still listed in NEVER_EXECUTED" in problems[0]
    # The reason is echoed back so the reader can judge whether it ever applied.
    assert "needs a live gateway process" in problems[0]


# --------------------------------------------------------------------------- #
# The floor
# --------------------------------------------------------------------------- #
def test_coverage_below_the_floor_fails_the_gate():
    """Guards the case no per-file rule can see: tests deleted wholesale."""
    problems = violations({"agentevolver/job/server.py": 1}, FLOOR_PERCENT - 0.1, registered={})

    assert len(problems) == 1
    assert "below the floor" in problems[0]


def test_coverage_exactly_at_the_floor_passes():
    """A floor is a floor. Off-by-one here makes the gate red the day it is introduced,
    which is the fastest way to have it removed."""
    assert violations({"agentevolver/job/server.py": 1}, FLOOR_PERCENT, registered={}) == []


def test_every_violation_is_reported_not_just_the_first():
    """Fixing one problem per run turns a single measured lane into four.

    Each measured run costs a full suite execution, so a gate that stops at the first
    violation is a gate that takes an afternoon to satisfy.
    """
    problems = violations(
        {"agentevolver/one.py": 0, "agentevolver/two.py": 0},
        FLOOR_PERCENT - 1.0,
        registered={},
    )

    assert len(problems) == 3        # two dark files, plus the floor


# --------------------------------------------------------------------------- #
# The gate runs early enough to change the exit code
# --------------------------------------------------------------------------- #
def test_the_gate_is_wired_to_the_test_loop_not_the_terminal_summary():
    """Wired late, the gate prints FAILED in red and still exits 0.

    This is not hypothetical — it is how the gate was written first, and it survived its
    own unit tests, a green full run, *and* an end-to-end probe that confirmed the red
    banner. Only checking `$?` caught it. pytest fixes the exit code from
    `session.testsfailed` the moment `pytest_runtestloop` returns, so anything hooked
    after that (`pytest_sessionfinish`, `pytest_terminal_summary`) is decoration.

    A failing gate that reports success is strictly worse than no gate: CI goes green
    while the log says otherwise, and everyone learns to trust the exit code.
    """
    from tests import conftest

    assert hasattr(conftest, "pytest_runtestloop"), (
        "the coverage gate must run inside pytest_runtestloop — see this test's docstring"
    )
    assert not hasattr(conftest, "pytest_terminal_summary"), (
        "pytest_terminal_summary runs after the exit code is decided; a gate there cannot fail a run"
    )


# --------------------------------------------------------------------------- #
# The register describes the repository that exists
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", sorted(NEVER_EXECUTED))
def test_every_registered_file_still_exists(path):
    """A register entry for a deleted file exempts nothing and looks like a decision.

    It also hides a real regression: recreate that path later and it is born exempt,
    never having earned the entry that covers it.
    """
    assert (_REPO_ROOT / path).is_file(), (
        f"{path} is registered in NEVER_EXECUTED but no longer exists — delete the entry"
    )


@pytest.mark.parametrize("path,reason", sorted(NEVER_EXECUTED.items()))
def test_every_registered_file_gives_a_reason(path, reason):
    """The reason is the whole cost of registering; without one the list is a mute
    allowlist that grows whenever a test is inconvenient to write."""
    assert len(reason) > 20, f"{path}: the reason must say why a test cannot reach it"
