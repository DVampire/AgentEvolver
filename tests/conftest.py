"""Shared pytest fixtures.

The test session runs against a throwaway tree, so generated state — staging
manifests, caches, the deploy registry, run checkpoints, knowledge bases,
workflow evidence — lands in a temp directory instead of the developer's
checkout.

``AGENTEVOLVER_HOME`` alone is not enough. ``process_general`` resolves
``project_root`` / ``workspace_root`` / ``log_root`` to absolute paths when the
config is first processed, and every manager that derives a ``base_dir`` from
``config.log_root`` captures one of those. An absolute path ignores the
override, so the roots are repointed as well — once, at session scope, before
the first manager is built.
"""

import json
import os
from pathlib import Path

import pytest

from agentevolver.config import config


@pytest.fixture(autouse=True, scope="session")
def _isolate_agentevolver_tree(tmp_path_factory):
    home = tmp_path_factory.mktemp("agentevolver-home")
    output = home / "output" / "test"
    patch = pytest.MonkeyPatch()
    patch.setenv("AGENTEVOLVER_HOME", str(home))
    patch.setattr(config, "project_root", str(output), raising=False)
    patch.setattr(config, "workspace_root", str(output / "workspace"), raising=False)
    patch.setattr(config, "log_root", str(output / "log"), raising=False)
    yield
    patch.undo()


# ---------------------------------------------------------------------------
# Repairing an interrupted mutation run
# ---------------------------------------------------------------------------
#: `test_consistency_checks_can_fail.py` proves each consistency check still catches its
#: defect by reintroducing the defect in real source and requiring the check to go red.
#: `finally` covers an exception; it does not cover SIGKILL or a ctrl-C at the wrong
#: instant, and a lost restore leaves the tree quietly broken — it happened once, and the
#: next full run reported sixteen unrelated failures with no hint of the cause.
#:
#: So the original bytes are parked beside the file before it is mutated, and the repair
#: below runs for *every* session. It lives here rather than in that test module because
#: a repair that only runs when you happen to invoke the damaged file's own test is not a
#: repair — the run that trips over the damage is usually a different one.
BACKUP_SUFFIX = ".mutation-backup"

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _restore_parked_originals() -> None:
    for backup in _REPO_ROOT.rglob(f"*{BACKUP_SUFFIX}"):
        target = Path(str(backup)[: -len(BACKUP_SUFFIX)])
        try:
            target.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            backup.unlink()
            print(f"restored {target.relative_to(_REPO_ROOT)} "
                  f"from an interrupted mutation run")
        except OSError as error:                                    # noqa: PERF203
            # Reported, never swallowed: a repair that fails silently leaves exactly the
            # state this exists to prevent, and the next failure looks unrelated.
            print(f"could not restore {target}: {error}")


#: Set by the parent when it launches a mutation subprocess. Without it the repair below
#: runs inside that subprocess and undoes the very defect the parent just introduced, so
#: the check under test sees clean source and passes — reported as "this check does not
#: catch its defect" when in fact nothing was ever wrong with it. The two mechanisms are
#: both right and cancel each other out; the parent is the one that knows.
#:
#: A subprocess killed while skipping the repair still leaves its backup behind, and the
#: next ordinary run restores it. Nothing is lost by opting out here.
SKIP_REPAIR_ENV = "AGENTEVOLVER_SKIP_MUTATION_REPAIR"


@pytest.fixture(scope="session", autouse=True)
def repair_interrupted_mutations():
    """Put back anything a killed mutation run left mutated — before and after."""
    if os.environ.get(SKIP_REPAIR_ENV):
        yield
        return
    _restore_parked_originals()
    yield
    _restore_parked_originals()


# ---------------------------------------------------------------------------
# The coverage gate
# ---------------------------------------------------------------------------
#: `coverage_gate.py` owns the rule; this runs it. It lives in a hook rather than in a
#: test because the answer does not exist until the session ends — a test that read the
#: report mid-run would be reading the *previous* run's file and quietly grading the
#: wrong thing.
#:
#: Both conditions below are load-bearing. Coverage is a property of a whole selection:
#: run `-k gateway` and every other file in the repo is at zero, which is not a finding
#: about the code. And a run with failures never reached the code those tests would have
#: executed, so its zeroes mean nothing either. In both cases the gate says so and stands
#: down, rather than reporting a defect it cannot actually see.


#: The one selection the register is calibrated against. `--cov` rewrites the default
#: marker expression to this, so `pytest --cov` is the whole command and there is no
#: second spelling of the lane whose numbers would disagree with the register.
#:
#: `slow` is dropped because measuring it is not possible and running it is not free: each
#: of those tests mutates real source and re-runs pytest in a fresh interpreter, and
#: subprocess coverage is deliberately not collected (see pyproject.toml). Leaving them in
#: multiplies the lane's runtime while adding nothing to the numbers it produces.
GATED_MARKEXPR = "not integration and not slow"


def pytest_configure(config):
    """Pin the measured run to one selection, so the register can mean something."""
    if getattr(config.option, "cov_source", None) and config.option.markexpr == "not integration":
        config.option.markexpr = GATED_MARKEXPR


def _coverage_plugin(config):
    """The active pytest-cov plugin, or None when the run is not measuring."""
    plugin = config.pluginmanager.get_plugin("_cov")
    if plugin is None or not getattr(config.option, "cov_source", None):
        return None
    return plugin if getattr(plugin, "cov_controller", None) else None


def _selection_is_whole_suite(config) -> bool:
    """True when this run measured the default selection rather than a subset."""
    if config.option.keyword:                    # -k
        return False
    if config.option.markexpr != GATED_MARKEXPR:      # -m, overriding the lane
        return False
    # Explicit paths: `pytest tests/test_job.py` measures one file's worth of imports.
    # Compared as resolved paths, not strings — `tests`, `tests/`, and an absolute path
    # are the same selection, and a string comparison silently disables the gate for two
    # of the three spellings anyone actually types.
    whole = {_REPO_ROOT, _REPO_ROOT / "tests"}
    for arg in config.args:
        if arg.startswith("-"):
            continue
        # `::` is a node id — always narrower than the whole suite.
        if "::" in arg or (Path(arg).resolve() not in whole):
            return False
    return True


@pytest.hookimpl(wrapper=True)
def pytest_runtestloop(session):
    """Fail a measured, complete, passing run that broke the coverage rule.

    Wrapping the *test loop* rather than implementing `pytest_terminal_summary` is not a
    style choice, and getting it wrong is invisible: pytest reads `session.testsfailed` to
    decide the exit code as soon as this loop returns, so a gate that fires any later
    prints its red banner and still exits 0. That is worse than no gate — CI reports a
    clean run while the terminal says FAILED. It was written the late way first, and the
    end-to-end probe below is what caught it. pytest-cov carries the same comment above
    the same hook, for the same reason.

    Ordering within the hook is what makes the data real: conftest is registered after
    pytest-cov, so this wrapper is the outer one and its post-`yield` half runs *after*
    pytest-cov has stopped and saved coverage.
    """
    from tests.coverage_gate import FLOOR_PERCENT, violations

    result = yield

    config = session.config
    plugin = _coverage_plugin(config)
    if plugin is None:
        return result

    reporter = config.pluginmanager.getplugin("terminalreporter")

    def say(text, **style):
        if reporter is not None:
            reporter.write_line(text, **style)

    if not _selection_is_whole_suite(config):
        say("coverage gate: skipped — this run measured a subset, and a file another "
            "test would have covered looks identical to a dead one.", yellow=True)
        return result
    if session.testsfailed:
        say("coverage gate: skipped — the run had failures, so its zeroes are "
            "unreached-because-broken, not unreached-because-dead.", yellow=True)
        return result

    # Generated here rather than trusting `--cov-report=json` to have been passed: the
    # gate must not be silently skippable by leaving a flag off. `source=` makes coverage
    # add files it never imported, which is exactly the set this gate is about.
    report_path = _REPO_ROOT / "output" / ".coverage.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        plugin.cov_controller.cov.json_report(outfile=str(report_path))
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as error:                                   # noqa: BLE001
        # Not re-raised: an exception out of this wrapper surfaces as an INTERNALERROR,
        # which reads like a broken pytest rather than a gate that could not run.
        say(f"coverage gate: could not read the report: {error}", red=True)
        session.testsfailed += 1
        return result

    # Files with no statements — a bare `__init__.py` — report zero covered lines while
    # having nothing to cover. Counting those as dark would fail every run forever.
    covered = {
        path: data["summary"]["covered_lines"]
        for path, data in report["files"].items()
        if data["summary"]["num_statements"] > 0
    }
    percent = report["totals"]["percent_covered"]

    problems = violations(covered, percent)
    if not problems:
        say(f"coverage gate: passed — {percent:.1f}% overall (floor {FLOOR_PERCENT:.1f}%), "
            f"no unregistered file left dark.", green=True)
        return result

    say("")
    say(f"coverage gate: FAILED ({len(problems)})", red=True, bold=True)
    for problem in problems:
        say(f"  - {problem}", red=True)
    # The exit code, not the banner, is what CI reads.
    session.testsfailed += 1
    return result
