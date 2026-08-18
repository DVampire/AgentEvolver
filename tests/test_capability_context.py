"""Every capability type a model can call reaches the prompt that may call it.

The rosters were assembled by a hand-written list of six method calls and rendered by
five hand-cut copies of one block across twenty-six templates. `agent` was missing from
the list, so prompts told the model to "dispatch a sub-agent from Available Sub-Agents"
while nothing produced that roster — the model had to infer it from the `*_agent` entries
in its tool schemas. `plugin` was missing from most of the blocks.

Both halves are derived now: the assembly walks `CAPABILITY_TYPES`, and one shared module
holds the blocks. These tests hold that derivation in place, because the failure it
replaces is silent in both directions — a type with no roster looks like a type with
nothing registered, and a slot no template renders looks like a slot with nothing in it.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from agentevolver.agent.types import Agent
from agentevolver.capability import CAPABILITY_TYPES

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "agentevolver" / "prompt"
MODULE = PROMPTS / "module" / "capability_context.html"

#: Environments render in their own block, beside the state that belongs to them: an
#: agent works *in* one rather than calling it in passing.
IN_ITS_OWN_BLOCK = {"environment"}


def _module_slots() -> set[str]:
    return set(re.findall(r"\{\{ *(available_\w+) *\}\}", MODULE.read_text(encoding="utf-8")))


@pytest.mark.parametrize("entry", [e for e in CAPABILITY_TYPES if e.type not in IN_ITS_OWN_BLOCK],
                         ids=lambda e: e.type)
def test_every_callable_type_has_a_slot_in_the_shared_block(entry):
    """A type registered, addressable and callable, and absent from the prompt, is a
    capability the model can only reach by guessing that it exists."""
    assert f"available_{entry.mount_type}" in _module_slots()


def test_the_block_names_no_slot_that_no_type_produces():
    """A slot for a type nobody registers renders empty forever and reads as a type with
    nothing in it — the same shape as the bug this file exists for, pointing the other way."""
    produced = {f"available_{entry.mount_type}" for entry in CAPABILITY_TYPES}
    assert _module_slots() <= produced, f"orphan slots: {_module_slots() - produced}"


@pytest.mark.parametrize("entry", list(CAPABILITY_TYPES), ids=lambda e: e.type)
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
    assert "for entry in CAPABILITY_TYPES" in source, (
        "capability contexts are assembled from a hand-written list again"
    )


def test_no_template_carries_its_own_copy_of_the_block():
    """Five variants stood in twenty-six templates, differing only by which types the
    agent had — which the roster already answers by being empty."""
    inline = [f.name for f in (PROMPTS / "default").glob("*.html")
              if "<capability-context>" in f.read_text(encoding="utf-8")]
    assert not inline, f"these inline the block instead of including the module: {inline}"


def test_every_template_that_lists_capabilities_includes_the_module():
    """A template that stopped including it would lose every roster at once, and the
    prompt would still render."""
    including = [f for f in (PROMPTS / "default").glob("*.html")
                 if "module/capability_context.html" in f.read_text(encoding="utf-8")]
    assert len(including) >= 26, f"only {len(including)} templates include the block"


def test_an_absent_roster_renders_nothing_rather_than_a_notice():
    """`[No workflows loaded.]` is a line the agent pays for on every step to be told
    about something it does not have. The block is conditional so it can be silent."""
    text = MODULE.read_text(encoding="utf-8")
    for slot in _module_slots():
        assert f"{{% if {slot} %}}" in text, f"{slot} renders unconditionally"


def test_the_code_mode_convention_rides_on_a_slot_that_is_rendered():
    """It was appended to `tool_context`, which no template names, so an agent holding
    `run_code_tool` was never told how to call anything from a program."""
    source = inspect.getsource(Agent._get_tool_context)
    assert "available_tools" in source and "tool_context" not in source.replace("_get_tool_context", "")


def test_the_capability_hook_cannot_collide_with_the_orchestration_state():
    """`agent` resolving to `_get_agent_context` would call the orchestration state with
    the wrong signature — at run time, and only for that one type."""
    source = inspect.getsource(Agent._capability_slots)
    assert '_capability_{entry.type}_slots' in source
    assert '_get_{entry.type}_context' not in source
