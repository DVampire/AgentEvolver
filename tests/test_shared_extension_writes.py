"""The shared extension library is written by promotion, and by nothing else.

Every file tool goes through `check_session_path`. `bash_tool` never did — it classifies
what a command *intends* (read-only, destructive, package management) but reads no paths —
so a redirect was the one write channel with no boundary at all. A generate run used it:
it wrote its workflow straight into the shared tree, promotion refused the file for being
outside the staging root, and the run failed at its last step having done all the work.

What makes this worth refusing is not access control. Promotion keeps four things in step
— `.versions/`, `.promotion-backups/`, `manifest.json`, and the validation that runs
before any of them — and a direct write updates none. The component is present and the
registry says it is not; the next promotion of that name overwrites it with no backup; a
rollback restores a version that was never recorded. So it is refused in every permission
mode, `danger_full_access` included: that mode says this machine's system commands are
trusted, which is a claim about the host, not a licence to bypass the framework's own
bookkeeping.

The check is deliberately incomplete and cannot be completed — a path built inside a
Python string, a heredoc or a subprocess is invisible to it. It catches a path written out
plainly, which is what an agent produces when it is following an instruction about where
to put something. That is the case that actually happens.
"""

from __future__ import annotations

import pytest

from agentevolver.paths import P, path_manager
from agentevolver.permission.types import PermissionMode, validate_command

MODES = [PermissionMode.WORKSPACE_WRITE, PermissionMode.DANGER_FULL_ACCESS]


@pytest.fixture
def shared():
    return path_manager.get(P.EXTENSION)


@pytest.mark.parametrize("mode", MODES)
def test_a_redirect_into_the_shared_tree_is_refused(mode, shared):
    """The exact shape a real run produced."""
    result = validate_command(f"echo x > {shared}/workflow/triage.html", mode)
    assert not result.allowed
    assert "promotion" in result.reason


@pytest.mark.parametrize("mode", MODES)
def test_danger_full_access_does_not_exempt_it(mode, shared):
    """Stated as its own test because the exemption is the natural thing to assume.

    Every other rule in `_validate_mode` returns early for this mode; the whole point here
    is that bypassing promotion is not a host-trust question.
    """
    result = validate_command(f"cp built.py {shared}/tool/built.py", mode)
    assert not result.allowed


def test_the_refusal_names_the_directory_the_run_should_have_used():
    """A block that only says "no" costs the run a step and teaches it nothing.

    The run has somewhere legitimate to write, and it is not a directory the agent can
    derive — so the message carries it.
    """
    path_manager.bind_session("local", "refusalmsg")
    try:
        staged = path_manager.session_roots()["extension"]
        shared = path_manager.get(P.EXTENSION)
        result = validate_command(f"echo x > {shared}/tool/x.py", PermissionMode.DANGER_FULL_ACCESS)
        assert not result.allowed
        assert str(staged) in result.reason
    finally:
        path_manager.unbind_session()


def test_the_runs_own_staging_tree_is_not_refused():
    """Where a generated component is *supposed* to go, so refusing it breaks generation."""
    path_manager.bind_session("local", "stagingok")
    try:
        staged = path_manager.session_roots()["extension"]
        result = validate_command(f"echo x > {staged}/workflow/t.html",
                                  PermissionMode.DANGER_FULL_ACCESS)
        assert result.allowed
    finally:
        path_manager.unbind_session()


def test_the_staging_tree_is_outside_the_shared_one_by_layout():
    """Why the test above passes, asserted directly rather than left to coincidence.

    The rule was first written with an explicit carve-out for the staging tree, and a
    mutation that deleted the carve-out changed nothing — the branch was unreachable,
    because the two trees do not overlap: staging hangs off `output/<owner>/sessions/<id>/`
    and the shared library off the project root. That separation is what keeps generation
    working, so it is what gets asserted. A layout change that nests one inside the other
    would block every generate run, and it fails here instead.
    """
    path_manager.bind_session("local", "layoutcheck")
    try:
        staged = path_manager.session_roots()["extension"].resolve()
        shared = path_manager.get(P.EXTENSION).resolve()
        assert shared not in staged.parents and staged != shared, (
            f"the staging tree {staged} sits inside the shared library {shared}; "
            f"every generated component would now be refused"
        )
    finally:
        path_manager.unbind_session()


@pytest.mark.parametrize("command", [
    "cat {shared}/tool/existing.py",
    "grep -r pattern {shared}/",
    "ls {shared}/skill",
    "python -m py_compile {shared}/tool/x.py",
])
def test_reading_the_shared_tree_stays_allowed(command, shared):
    """Refusing reads would break the thing agents legitimately do most.

    A run generating a tool reads the registered ones to match their shape; an evaluate
    run reads the component it is judging. Neither writes.
    """
    result = validate_command(command.format(shared=shared), PermissionMode.DANGER_FULL_ACCESS)
    assert result.allowed, result.reason


@pytest.mark.parametrize("command", [
    "echo x > /tmp/scratch.txt",
    "echo x > relative/output.txt",
    "pip install --target /opt/deps requests",
    "make && ./run-tests",
    "python script.py > results.json",
])
def test_writes_everywhere_else_are_untouched(command):
    """The scope of the rule, asserted rather than described.

    This is one protected directory, not a sandbox. A run still needs `/tmp`, still
    installs packages, still compiles in the checkout — and the container is what bounds
    those, where there is one.
    """
    result = validate_command(command, PermissionMode.DANGER_FULL_ACCESS)
    assert result.allowed, result.reason


def test_what_the_check_cannot_see_is_documented_rather_than_pretended(shared):
    """A path built inside a Python string is invisible here, and always will be.

    Asserted so the limit is a recorded property instead of a surprise. Anyone reading
    this as a security boundary would be wrong; the boundary is the container, and this is
    a guard against writing to the wrong place.
    """
    hidden = f"python -c \"open('{shared}/tool/x.py','w').write('')\""
    assert validate_command(hidden, PermissionMode.DANGER_FULL_ACCESS).allowed


def test_a_command_naming_no_absolute_path_costs_nothing(shared):
    """The common case is a command with no absolute path in it at all."""
    from agentevolver.permission.types import _written_paths

    assert _written_paths("ls -la") == []
    assert _written_paths("grep -rn pattern src/") == []
