"""Every capability type a model can call reaches the prompt that may call it.

The rosters were assembled by a hand-written list of six method calls and rendered by
five hand-cut copies of one block across twenty-six templates. `agent` was missing from
the list, so prompts told the model to "dispatch a sub-agent from Available Sub-Agents"
while nothing produced that roster — the model had to infer it from the `*_agent` entries
in its tool schemas. `plugin` was missing from most of the blocks.

Both halves are derived now: the assembly walks `MOUNTED_TYPES`, and one shared module
holds the blocks. These tests hold that derivation in place, because the failure it
replaces is silent in both directions — a type with no roster looks like a type with
nothing registered, and a slot no template renders looks like a slot with nothing in it.
"""

from __future__ import annotations

import inspect
import pathlib
import re
from pathlib import Path

import pytest

from agentevolver.agent.types import Agent
from agentevolver.capability import MOUNTED_TYPES

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "agentevolver" / "prompt"
MODULE = PROMPTS / "module" / "capability_context.html"

#: Environments render in their own block, beside the state that belongs to them: an
#: agent works *in* one rather than calling it in passing.
IN_ITS_OWN_BLOCK = {"environment"}


def _module_slots() -> set[str]:
    return set(re.findall(r"\{\{ *(available_\w+) *\}\}", MODULE.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "entry", [e for e in MOUNTED_TYPES if e.type not in IN_ITS_OWN_BLOCK], ids=lambda e: e.type
)
def test_every_callable_type_has_a_slot_in_the_shared_block(entry):
    """A type registered, addressable and callable, and absent from the prompt, is a
    capability the model can only reach by guessing that it exists."""
    assert f"available_{entry.mount_type}" in _module_slots()


def test_the_block_names_no_slot_that_no_type_produces():
    """A slot for a type nobody registers renders empty forever and reads as a type with
    nothing in it — the same shape as the bug this file exists for, pointing the other way."""
    produced = {f"available_{entry.mount_type}" for entry in MOUNTED_TYPES}
    assert _module_slots() <= produced, f"orphan slots: {_module_slots() - produced}"


@pytest.mark.parametrize("entry", list(MOUNTED_TYPES), ids=lambda e: e.type)
def test_every_type_can_answer_the_roster_call(entry):
    """The generic builder asks the manager named by the table. `agent` had no
    `get_instruction`, which is the whole reason sub-agents never reached a prompt."""
    manager = entry.manager()
    assert hasattr(manager, "get_instruction"), f"{entry.type} manager cannot render a roster"
    parameters = inspect.signature(manager.get_instruction).parameters
    for expected in ("allowlist", "level"):
        assert expected in parameters, f"{entry.type}.get_instruction lacks {expected}"


def test_the_assembly_walks_the_table_rather_than_a_list():
    """Read from the source: a list here is the register that went stale, and it goes
    stale silently — the type simply does not appear."""
    source = inspect.getsource(Agent._get_messages)
    assert "for entry in MOUNTED_TYPES" in source, (
        "capability contexts are assembled from a hand-written list again"
    )


def test_no_template_carries_its_own_copy_of_the_block():
    """Five variants stood in twenty-six templates, differing only by which types the
    agent had — which the roster already answers by being empty."""
    inline = [
        f.name
        for f in (PROMPTS / "default").glob("*.html")
        if "<capability-context>" in f.read_text(encoding="utf-8")
    ]
    assert not inline, f"these inline the block instead of including the module: {inline}"


def test_every_template_that_lists_capabilities_includes_the_module():
    """A template that stopped including it would lose every roster at once, and the
    prompt would still render.

    Derived from the templates rather than counted: this asserted `>= 26`, which stopped
    meaning anything the moment twenty-one prompts were merged into three. What makes a
    template one that lists capabilities is that it renders an agent's state at all — so
    every template including `agent_context` must include this block too.
    """
    missing = []
    for f in sorted((PROMPTS / "default").glob("*.html")):
        text = f.read_text(encoding="utf-8")
        if "module/agent_context.html" in text and "module/capability_context.html" not in text:
            missing.append(f.name)
    assert not missing, f"these render agent state but no capability roster: {missing}"


def test_an_absent_roster_renders_nothing_rather_than_a_notice():
    """`[No workflows loaded.]` is a line the agent pays for on every step to be told
    about something it does not have. The block is conditional so it can be silent."""
    text = MODULE.read_text(encoding="utf-8")
    for slot in _module_slots():
        assert f"{{% if {slot} %}}" in text, f"{slot} renders unconditionally"


def test_the_code_mode_convention_rides_on_a_slot_that_is_rendered():
    """It was appended to `tool_context`, which no template names, so an agent holding
    `batch_call_tool` was never told how to call anything from a program."""
    source = inspect.getsource(Agent._get_tool_context)
    assert "available_tools" in source and "tool_context" not in source.replace(
        "_get_tool_context", ""
    )


def test_the_capability_hook_cannot_collide_with_the_orchestration_state():
    """`agent` resolving to `_get_agent_context` would call the orchestration state with
    the wrong signature — at run time, and only for that one type."""
    source = inspect.getsource(Agent._capability_slots)
    assert "_capability_{entry.type}_slots" in source
    assert "_get_{entry.type}_context" not in source


# ---------------------------------------------------------------------------
# The catalog is produced in Python and rendered by a stylesheet and a script.
# Three lists of the same types, in three languages that cannot import each other.
# ---------------------------------------------------------------------------

VISUAL = ROOT / "agentevolver" / "visual"


def _rendered_leaf_tags() -> set[str]:
    """The catalog tags the prompt actually emits — module block plus the env block."""
    tags = set(re.findall(r"<(\w+-context)>", MODULE.read_text(encoding="utf-8")))
    env = PROMPTS / "module" / "environment_context.html"
    tags |= set(re.findall(r"<(\w+-context)>", env.read_text(encoding="utf-8")))
    return tags


def test_the_script_cardifies_every_catalog_the_prompt_emits():
    """A catalog nobody cardified renders as one unbroken wall of markdown.

    The script listed four while the prompt emitted seven. The three it missed —
    sub-agents, plugins, environments — are catalogs a reader most needs to scan, and
    the failure is silent: the text is all there, just unreadable.
    """
    script = (VISUAL / "js" / "prompt.js").read_text(encoding="utf-8")
    listed = set(re.findall(r"'(\w+-context)'", script))
    missing = _rendered_leaf_tags() - listed
    assert not missing, (
        f"the prompt emits these catalogs and prompt.js never cardifies them: {sorted(missing)}"
    )


def test_the_stylesheet_labels_every_catalog_the_prompt_emits():
    """An unlabelled catalog is indistinguishable from one that failed to load.

    The label carries the live count, which is the only thing separating "no plugins
    are mounted" from "the plugin roster did not render".
    """
    css = (VISUAL / "css" / "prompt.css").read_text(encoding="utf-8")
    labelled = set(re.findall(r"(\w+-context)::before", css))
    missing = _rendered_leaf_tags() - labelled
    assert not missing, f"these catalogs render without a label: {sorted(missing)}"


def test_environments_reach_a_prompt_through_the_shared_module():
    """The one type that was still hand-written, in three byte-identical copies.

    All three sat *below* `</agent-context>`, so the one catalog that never changes
    between steps was the one placed after the state that always does — re-read at full
    price every step.
    """
    inline = [
        f.name
        for f in (PROMPTS / "default").glob("*.html")
        if "<environment-context>" in f.read_text(encoding="utf-8")
    ]
    assert not inline, f"these still inline the environment block: {inline}"

    for f in sorted((PROMPTS / "default").glob("*.html")):
        text = f.read_text(encoding="utf-8")
        if "module/environment_context.html" not in text:
            continue
        assert text.index("module/environment_context.html") < text.index(
            "module/agent_context.html"
        ), f"{f.name} renders its environments after its state"


def test_the_environment_block_renders_only_when_the_agent_has_one():
    """Rendered, not just included — an `{% if %}` guarding the wrong name is silent.

    The block would simply never appear, which reads exactly like an agent with no
    environments; the roster this file exists to deliver would be missing again.
    """
    from jinja2 import Template

    source = (PROMPTS / "module" / "environment_context.html").read_text(encoding="utf-8")
    body = Template(source).render(
        environment_context="### Available Environments\n## browser_environment"
    )
    assert "<environment-context>" in body and "browser_environment" in body

    empty = Template(source).render(environment_context="")
    assert "<environment-context>" not in empty, (
        "an agent with no environments pays for a block saying so"
    )


def test_an_environment_backed_agent_reports_its_state_through_one_slot():
    """One thing, one spelling.

    The live state of the environment an agent works in was `browser_state` in one agent,
    `remote_state` in another, and rendered under a third tag, `desktop-state`, in a
    third. Two of the three had no stylesheet rule, so the block they were introducing
    rendered unlabelled — and each new environment-backed agent invented a fourth name
    because there was nothing saying which one to use.
    """
    actors = ROOT / "agentevolver" / "agent" / "actor"
    offenders = []
    for path in sorted(actors.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for stale in ('base["remote_state"]', 'base["browser_state"]'):
            if stale in text:
                offenders.append(f"{path.name}: {stale}")
    assert not offenders, (
        f"these fill a private state slot instead of `environment_state`: {offenders}"
    )

    templates = ROOT / "agentevolver" / "prompt" / "default"
    stray = [
        f.name
        for f in templates.glob("*.html")
        if re.search(r"<(remote|browser|desktop)-state>", f.read_text(encoding="utf-8"))
    ]
    assert not stray, f"these render their own state tag instead of `environment-state`: {stray}"


def test_every_block_a_prompt_opens_in_the_system_turn_is_styled():
    """An unstyled block renders with no label and no section number.

    Every rule block in the system turn is numbered and colour-coded by the stylesheet,
    from a hand-written list of tag names. A prompt that opens a block the list does not
    know renders it as an anonymous grey slab — which is how `browser-state-rules`,
    `desktop-state-rules` and `remote-state-rules` all shipped.

    Modules are resolved first. Scanning the raw templates misses every block a shared
    module contributes, which is how `runtime`, `progress-rules` and `response-protocol`
    — three blocks in *every* prompt — went unlabelled while a check on the same rule
    passed.
    """
    css = (VISUAL / "css" / "prompt.css").read_text(encoding="utf-8")
    styled = set(re.findall(r"div\.system > ([\w-]+)", css))

    def resolve(text: str, base: pathlib.Path) -> str:
        def sub(match):
            path = (base / match.group(1)).resolve()
            return resolve(path.read_text(encoding="utf-8"), path.parent) if path.exists() else ""

        return re.sub(r'<module src="([^"]+)"></module>', sub, text)

    unstyled = {}
    for path in sorted((PROMPTS / "default").glob("*.html")):
        system = resolve(path.read_text(encoding="utf-8"), path.parent).split('<div class="user">')[
            0
        ]
        for tag in re.findall(r"<([a-z][\w-]+)>", system):
            if "-" in tag and tag not in styled:
                unstyled.setdefault(tag, []).append(path.name)
    assert not unstyled, (
        "these rule blocks render unlabelled; add them to prompt.css or reuse an "
        f"existing block name: { {k: v for k, v in unstyled.items()} }"
    )
