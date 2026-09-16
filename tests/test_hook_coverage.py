"""Every hook event has a firing site, and every site names it through the enum.

Both halves of that had already failed, and neither failed loudly.

Eleven of twenty-five events were declared and never raised: `pre_messages`,
`notification`, `config_change`, `on_escalate`, `subagent_start`, `subagent_stop` and
the four tool-use events. A handler registering for one of those looks like a policy in
force and is simply never called, which is indistinguishable from a policy that allows
everything.

At the same time two real firing sites — the tool execution pipeline and the sandbox
worktree — raised their events as bare strings, because `HookEvent` lived beside models
that import the message and session packages and importing it from those layers closed a
cycle. A bare string does not fail when it drifts; a typo becomes an event nobody raises.
That is why the enum is now a dependency-free leaf module.
"""

import re
from pathlib import Path

import pytest

from agentevolver.hook.events import HookEvent

PACKAGE_ROOT = Path(__file__).parents[1] / "agentevolver"
#: The enum's own definition is not a firing site.
DEFINITION = PACKAGE_ROOT / "hook" / "events.py"


def _sources():
    return [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if path != DEFINITION and "__pycache__" not in path.parts
    ]


def _referenced():
    """Event names reached through the enum, anywhere in the package."""
    seen = set()
    for path in _sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        seen.update(re.findall(r"HookEvent\.([A-Z_]+)", text))
    return seen


def test_every_declared_event_is_raised_somewhere():
    declared = {member.name for member in HookEvent}
    missing = sorted(declared - _referenced())
    assert not missing, (
        f"declared but never raised: {missing} — give each one a firing site or delete "
        "it; a handler cannot tell an event that never fires from one that always allows"
    )


def test_the_enum_is_importable_without_the_models_it_used_to_live_beside():
    """A leaf module, so any layer can name an event.

    The tool pipeline and the sandbox both hit a cycle importing it from `hook.types`
    and fell back to bare strings. This is the property that stops that recurring.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; from agentevolver.hook.events import HookEvent; "
         "assert HookEvent.PRE_INVOKE; "
         "assert 'agentevolver.session' not in sys.modules, 'pulled in the session package'; "
         "assert 'agentevolver.message' not in sys.modules, 'pulled in the message package'"],
        capture_output=True, text=True, cwd=PACKAGE_ROOT.parent,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("event", list(HookEvent))
def test_an_event_name_matches_its_member(event):
    """`HookEvent.PRE_STEP == "pre_step"`, so a bare string and the enum are one key.

    They have to be: `hook_manager` dispatches on the value, and two of the firing
    sites were written against strings for years.
    """
    assert event.value == event.name.lower()


def test_no_firing_site_names_an_event_with_a_bare_string():
    """A drifting string is a silent miss; the enum is what makes a typo an error."""
    values = {member.value for member in HookEvent}
    offenders = []
    for path in _sources():
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if "emit(" not in line and "broadcast(" not in line:
                continue
            for literal in re.findall(r"""["']([a-z_]+)["']""", line):
                if literal in values:
                    offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{number} {literal!r}")
    assert not offenders, (
        "these raise an event by bare string; use HookEvent so a typo fails:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.asyncio
async def test_initializing_the_manager_registers_the_built_in_hooks():
    """Discovery must name the module that defines the hooks, not the package.

    `_load_from_registry` populated the HOOK registry with `import agentevolver.hook`,
    relying on the package's import side effect. Making that package hand its heavy
    names out lazily — so any layer could name an event without closing an import cycle
    — meant the import populated nothing, and every built-in hook silently failed to
    register: no trace, no trajectory, no budget, no plan mode, no project memory.

    Nothing failed. A run with no observers looks exactly like a run whose observers had
    nothing to say, which is why this asserts on the names rather than on a count.
    """
    from agentevolver.hook import hook_manager

    await hook_manager.initialize()
    registered = set(hook_manager.list())
    expected = {
        "trace_hook", "trajectory_hook", "constraint_hook",
        "plan_mode_hook", "project_memory_hook", "registration_hook", "compact",
    }
    assert expected <= registered, f"never registered: {sorted(expected - registered)}"


def test_every_named_observer_is_a_hook_that_exists():
    """`events.emit` calls two hooks by name; a typo there is a silent no-op.

    Nothing verifies a name at the call site — `hook_manager(name=...)` on an unknown
    name returns rather than raises, so the loop would keep running with no observers.
    """
    import inflection

    import agentevolver.hook.default  # noqa: F401 - registers the built-ins
    from agentevolver.agent.loop.events import OBSERVERS
    from agentevolver.registry import HOOK

    known = {
        (getattr(cls.model_fields.get("name"), "default", None)
         or inflection.underscore(cls.__name__))
        for cls in HOOK.module_dict.values()
    }
    unknown = sorted(set(OBSERVERS) - known)
    assert not unknown, f"OBSERVERS names hooks that do not exist: {unknown}"
