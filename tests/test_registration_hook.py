"""One hook installs all eight component types, and the table says how each differs.

Eight registration hooks used to exist as eight near-copies, which is how one type ended
up with no hook at all — a generated plugin was written, reported as created, and never
installed — and how only one of them read a path out of backticks. They are now one hook
and one table, so the tests that matter are: does the table still cover every type the
framework evolves, and does each row still describe its type's real shape.

The table is what a ninth component type will forget to update. `test_every_evolvable...`
is where that shows up, at import time in CI, rather than at the end of a generate run
that did all the work and then could not install it.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import pytest

from agentevolver.capability.types import COMPONENT_TYPES, component_type
from agentevolver.hook.default.registration import (
    SHAPES,
    RegistrationHook,
    _mentions,
    resolve_artifact,
    shape_for,
)
from agentevolver.hook.types import HookContext, HookDecision


def _ctx(**payload: Any) -> HookContext:
    return HookContext(id="reg-test", name="registration_hook", input=payload)


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #
def test_every_evolvable_component_type_can_be_installed():
    """Generation is offered for exactly the types installation handles.

    A type present in one and absent from the other is the plugin bug: a run builds the
    thing, and finds out at its last step that nothing knows how to install it.
    """
    from agentevolver.extension import EVOLVABLE_MODULES

    installable = {e.type for e in COMPONENT_TYPES if shape_for(e.type) is not None}
    assert installable == set(EVOLVABLE_MODULES), (
        f"installable but not evolvable: {sorted(installable - set(EVOLVABLE_MODULES))}; "
        f"evolvable but not installable: {sorted(set(EVOLVABLE_MODULES) - installable)}"
    )


def test_only_the_two_types_that_need_to_differ_carry_a_behaviour_row():
    """Six of the eight install identically, and a row that says nothing goes stale.

    `SHAPES` used to hold one row per type, six of them repeating the defaults — and the
    artifact shape as well, which promotion held a second copy of and got wrong.
    """
    assert set(SHAPES) == {"agent", "workflow"}
    for module in ("tool", "skill", "connector", "plugin", "environment", "memory"):
        assert shape_for(module) is shape_for("tool"), f"{module} takes the default"


def test_promotion_walks_every_component_type():
    """The bug this refactor was chasing.

    `ProjectSandbox` kept its own list of promotable modules and it stopped at six, so a
    generated workflow, plugin or memory was registered and then refused by promotion —
    "Requested staged extension component was not found", for a file sitting right there.
    Both now read the capability table.
    """
    from agentevolver.sandbox.project import _promotable_shapes

    promotable = set(_promotable_shapes())
    missing = {e.type for e in COMPONENT_TYPES} - promotable
    assert not missing, f"generated but never promotable: {sorted(missing)}"
    assert "prompt" in promotable, (
        "an agent's prompt is promoted beside the agent; promotion has to know its shape"
    )


def test_promotion_and_installation_agree_on_what_each_type_looks_like():
    """Two readers of one fact, checked against each other rather than trusted.

    They disagreed for as long as each had its own copy, and the disagreement was silent:
    one list simply had fewer entries than the other.
    """
    from agentevolver.sandbox.project import _promotable_shapes

    shapes = _promotable_shapes()
    for entry in COMPONENT_TYPES:
        assert shapes[entry.type] == (entry.directory, entry.suffix), (
            f"{entry.type}: promotion sees {shapes[entry.type]}, "
            f"the capability table says {(entry.directory, entry.suffix)}"
        )


def test_there_is_exactly_one_registration_hook():
    """The merge, asserted rather than assumed.

    Eight names in the registry meant eight copies of one algorithm; a reappearing second
    one means a type was special-cased by forking the hook again instead of adding a row.
    """
    from agentevolver.registry import HOOK
    import agentevolver.hook  # noqa: F401  — registers the defaults

    registered = sorted(
        name for name in (
            getattr(cls, "model_fields", {}).get("name").default
            for cls in HOOK.module_dict.values()
            if "name" in getattr(cls, "model_fields", {})
        )
        if isinstance(name, str) and "registration" in name
    )
    assert registered == ["registration_hook"], registered


def test_the_dispatcher_asks_for_that_one_hook_by_name():
    """`register_generated` names the hook it fires. A stale `{type}_registration_hook`
    there would resolve to nothing and fail every run, for every type at once."""
    import inspect
    from agentevolver.hook import promotion

    source = inspect.getsource(promotion.register_generated)
    assert 'name="registration_hook"' in source
    assert '"target_type": target' in source, (
        "the hook selects its row by target_type; the dispatcher must pass it"
    )


@pytest.mark.parametrize("module,directory,entry,suffix", [
    ("tool", False, "", ".py"),
    ("memory", False, "", ".py"),
    ("agent", False, "", ".py"),
    ("workflow", False, "", ".html"),
    ("skill", True, "", ".py"),
    ("connector", True, "", ".py"),
    ("plugin", True, "plugin.py", ".py"),
    ("environment", True, "environment.py", ".py"),
])
def test_each_type_declares_its_real_artifact_shape(module, directory, entry, suffix):
    """Checked one by one, because these are what every reader of the table relies on:
    a skill is a directory, a workflow is `.html`, an environment must hold the file its
    loader reads."""
    declared = component_type(module)
    assert (declared.directory, declared.entry, declared.suffix) == (directory, entry, suffix)


# --------------------------------------------------------------------------- #
# Finding the artifact
# --------------------------------------------------------------------------- #
def _staged(root, module: str, *parts: str):
    """A path inside the bound session's staging tree, with its parent created."""
    path = root.joinpath(module, *parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def promotion_log(monkeypatch):
    """Stand in for the promotion step, recording when it ran.

    Any root that is not the shared one is a staging tree, so a bound test session always
    takes the staged branch. Promotion itself copies between real project roots, which is
    not what these tests are about — but *when* it runs is: a type that rewrites its
    artifact must do it to the staged copy, so what gets promoted is what was validated.

    `autouse` because the real function writes into the machine's shared extension tree.
    Left opt-in, it ran for one test that had not asked for the fixture and left two stub
    files — `# tool`, `# agent` — installed in the working copy. A safeguard that has to
    be remembered on every new test is one that will be forgotten on some new test.
    """
    order: list[tuple[str, str]] = []
    monkeypatch.setattr("agentevolver.sandbox.project.validate_staged_extension",
                        lambda root: order.append(("validate", "")) or {})

    def _promote(root, path):
        # The artifact as it stood at promotion time, not as it ends up: the two differ
        # for any type that rewrites its own file, and which one gets promoted is the
        # whole question.
        from pathlib import Path
        try:
            snapshot = Path(path).read_text(encoding="utf-8")
        except OSError:
            snapshot = ""
        order.append(("promote", snapshot))
        return path

    monkeypatch.setattr("agentevolver.hook.promotion.promote_approved_component", _promote)
    return order


@pytest.fixture(autouse=True)
def staging(bound_session, monkeypatch):
    """Point the staged-path fallback at this run's staging tree.

    `resolve_artifact` falls back to `extension_manager.stage_path(module, leaf)` when a
    run named no path at all. Unpatched, that reads the machine's actual extension tree,
    so a test asserting "nothing resolves" passes or fails on what happens to be
    installed.
    """
    from agentevolver.extension import extension_manager
    root = bound_session["extension"]
    monkeypatch.setattr(
        extension_manager, "stage_path",
        lambda module, leaf: str(root / module / leaf),
    )


def test_a_file_type_is_found_from_a_path_in_prose(bound_session):
    artifact = _staged(bound_session["extension"], "tool", "web_search_tool.py")
    artifact.write_text("# tool")
    found = resolve_artifact(
        module="tool", target_name=None,
        reasoning=f"Wrote the tool to extension/tool/web_search_tool.py and verified it.",
        extension_root=str(bound_session["extension"]),
        matches=_mentions("tool", component_type("tool")),
    )
    assert found == str(artifact)


def test_a_directory_type_is_found_when_the_run_names_its_entry_file(bound_session):
    """The generate skill tells a run either spelling is fine, and the loader wants the
    directory — so naming `environment.py` has to resolve up to the directory holding it.
    """
    directory = _staged(bound_session["extension"], "environment", "shell_env", "environment.py")
    directory.write_text("# env")
    found = resolve_artifact(
        module="environment", directory=True, entry="environment.py", target_name=None,
        reasoning="Created `extension/environment/shell_env/environment.py`.",
        extension_root=str(bound_session["extension"]),
        matches=_mentions("environment", component_type("environment")),
    )
    assert found == str(directory.parent)


def test_a_directory_missing_its_entry_file_is_not_accepted(bound_session):
    """Accepting it defers the failure to load time, where the message names neither the
    run that produced it nor the file it lacks."""
    directory = _staged(bound_session["extension"], "plugin", "notes", "README.md")
    directory.write_text("# notes")
    assert resolve_artifact(
        module="plugin", directory=True, entry="plugin.py", target_name=None,
        reasoning="Created extension/plugin/notes/",
        extension_root=str(bound_session["extension"]),
        matches=_mentions("plugin", component_type("plugin")),
    ) is None


def test_a_source_file_the_run_merely_quoted_is_not_registered(tmp_path, bound_session):
    """Prose names the artifact alongside everything the run read to write it.

    Without the `extension/` requirement, a run that says "modelled on
    agentevolver/tool/default/inspect.py" gets *that* file registered as its output.
    """
    quoted = tmp_path / "agentevolver" / "tool" / "default" / "inspect.py"
    quoted.parent.mkdir(parents=True)
    quoted.write_text("# framework source")
    assert resolve_artifact(
        module="tool", target_name=None,
        reasoning=f"Modelled it on {quoted}, then wrote mine.",
        extension_root=str(bound_session["extension"]),
        matches=_mentions("tool", component_type("tool")),
    ) is None


def test_a_path_from_another_module_does_not_resolve(bound_session):
    """A skill run naming its own `references/tool.md` must not install a tool."""
    other = _staged(bound_session["extension"], "tool", "unrelated.py")
    other.write_text("# not mine")
    assert resolve_artifact(
        module="skill", directory=True, target_name=None,
        reasoning=f"See {other} for the pattern.",
        extension_root=str(bound_session["extension"]),
        matches=_mentions("skill", component_type("skill")),
    ) is None


def test_a_structured_path_is_believed_without_the_prose_filter(tmp_path, bound_session):
    """`artifact_path` is the run stating which file it means, not a mention to sift out
    of a paragraph — so it is accepted wherever it points, including outside `extension/`.
    """
    artifact = tmp_path / "elsewhere" / "custom_tool.py"
    artifact.parent.mkdir()
    artifact.write_text("# tool")
    assert resolve_artifact(
        module="tool", target_name=None, artifact_path=str(artifact),
        extension_root=str(bound_session["extension"]),
        matches=_mentions("tool", component_type("tool")),
    ) == str(artifact)


# --------------------------------------------------------------------------- #
# The hook itself
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_unknown_target_type_is_blocked_with_the_list_of_known_ones():
    """The one failure the run can act on directly, so the message names the choices."""
    result = await RegistrationHook().handle(_ctx(target_type="widget", target_name="x"))
    assert result.decision == HookDecision.BLOCK
    assert "widget" in result.reason
    for module in SHAPES:
        assert module in result.reason


@pytest.mark.asyncio
async def test_a_missing_target_type_is_blocked_rather_than_defaulted():
    """Defaulting picks a module at random and reports 'file not found' for a run whose
    real problem is that nobody said what it built."""
    result = await RegistrationHook().handle(_ctx(target_name="x"))
    assert result.decision == HookDecision.BLOCK
    assert "target_type" in result.reason


@pytest.mark.asyncio
async def test_a_run_whose_artifact_cannot_be_found_is_told_what_to_include(bound_session):
    result = await RegistrationHook().handle(_ctx(
        target_type="tool", target_name="missing_tool",
        reasoning="I wrote the tool.",
    ))
    assert result.decision == HookDecision.BLOCK
    assert "done_tool reasoning" in result.reason


@pytest.mark.asyncio
async def test_a_registered_component_reports_allow(bound_session, monkeypatch, promotion_log):
    """The success path, with the extension manager stood in for.

    What is asserted is the handoff: the module the row named, the path that was resolved,
    and the evolvable config that lets a later round optimize what this one produced.
    """
    artifact = _staged(bound_session["extension"], "tool", "adder_tool.py")
    artifact.write_text("# tool")
    seen: Dict[str, Any] = {}

    async def _add(module, path, config=None):
        seen.update(module=module, path=path, config=config)
        return "adder_tool"

    from agentevolver.extension import extension_manager
    monkeypatch.setattr(extension_manager, "add_component", _add)

    result = await RegistrationHook().handle(_ctx(
        target_type="tool", target_name="adder_tool",
        reasoning="Wrote extension/tool/adder_tool.py.",
    ))
    assert result.decision == HookDecision.ALLOW
    assert seen == {"module": "tool", "path": str(artifact),
                    "config": {"enable_evolving": True}}


@pytest.mark.asyncio
async def test_an_agent_is_constructed_with_a_workspace_and_a_model(bound_session, monkeypatch, promotion_log):
    """The row `agent` exists for: a tool is loaded, an agent is instantiated."""
    artifact = _staged(bound_session["extension"], "agent", "triage_agent.py")
    artifact.write_text("# agent")
    seen: Dict[str, Any] = {}

    async def _add(module, path, config=None):
        if module == "agent":
            seen.update(config=config)
        return "triage_agent"

    from agentevolver.extension import extension_manager
    monkeypatch.setattr(extension_manager, "add_component", _add)

    result = await RegistrationHook().handle(_ctx(
        target_type="agent", target_name="triage_agent", model_name="llm_hub/claude-opus-5",
        reasoning="Wrote extension/agent/triage_agent.py.",
    ))
    assert result.decision == HookDecision.ALLOW
    assert seen["config"]["model_name"] == "llm_hub/claude-opus-5"
    assert seen["config"]["enable_evolving"] is True
    assert seen["config"]["base_dir"]


@pytest.mark.asyncio
async def test_an_agent_evolution_that_only_changed_the_prompt_still_registers(bound_session, monkeypatch, promotion_log):
    """An optimizer's remit is the class, the prompt, or both.

    Blocking a prompt-only change for a `.py` it never needed to write rejects the run for
    succeeding at the narrower thing it set out to do — and the change is already on disk.
    """
    prompt = _staged(bound_session["extension"], "prompt", "triage_agent.html")
    prompt.write_text("<div></div>")
    registered = []

    async def _add(module, path, config=None):
        registered.append((module, path))
        return "triage_agent"

    from agentevolver.extension import extension_manager
    monkeypatch.setattr(extension_manager, "add_component", _add)

    result = await RegistrationHook().handle(_ctx(
        target_type="agent", target_name="triage_agent",
        reasoning="Rewrote the prompt at extension/prompt/triage_agent.html; no class change.",
    ))
    assert result.decision == HookDecision.ALLOW
    assert registered == [("prompt", str(prompt))]


@pytest.mark.asyncio
async def test_a_workflow_is_activated_and_compiled_before_it_is_registered(bound_session, monkeypatch, promotion_log):
    """The `workflow` row's whole reason: the artifact is rewritten before promotion, so
    what gets promoted is what compiled."""
    artifact = _staged(bound_session["extension"], "workflow", "review.html")
    artifact.write_text(
        "<!DOCTYPE html><html><body><workflow name='review'>"
        "<flow><checkpoint /></flow></workflow></body></html>"
    )

    async def _add(module, path, config=None):
        return "review"

    from agentevolver.extension import extension_manager
    monkeypatch.setattr(extension_manager, "add_component", _add)

    result = await RegistrationHook().handle(_ctx(
        target_type="workflow", target_name="review",
        artifact_path=str(artifact),
    ))
    assert result.decision == HookDecision.ALLOW
    rewritten = artifact.read_text()
    assert 'status="active"' in rewritten
    assert 'enable-evolving="true"' in rewritten
    assert [step for step, _ in promotion_log] == ["validate", "promote"]
    promoted = dict(promotion_log)["promote"]
    assert 'status="active"' in promoted, (
        "the artifact was promoted before it was activated and compiled, so the shared "
        "tree can receive a workflow that never compiled"
    )


@pytest.mark.asyncio
async def test_a_workflow_that_is_not_a_document_is_blocked_with_the_reason(bound_session):
    """A bare `<workflow>` fragment is the shape a run reaches for first, and it would be
    registered as active without ever compiling. The message has to say which."""
    artifact = _staged(bound_session["extension"], "workflow", "fragment.html")
    artifact.write_text("<workflow name='x'><flow><checkpoint /></flow></workflow>")

    result = await RegistrationHook().handle(_ctx(
        target_type="workflow", target_name="fragment",
        artifact_path=str(artifact),
    ))
    assert result.decision == HookDecision.BLOCK
    assert "DOCTYPE" in result.reason
