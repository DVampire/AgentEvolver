"""A panel can be added to the conversation view without editing it.

The four shipped panels were imported and placed by `Conversation.tsx` directly. That
works for panels we write and forecloses on panels we do not: adding one from outside
this repository meant editing that file, which meant forking it — an awkward thing for a
project that means to be infrastructure as well as a product.

What is here is deliberately the smallest thing that removes the fork: a named registry
and a `<Slot>` that renders it. Not the upstream slot system — scoped stores, declaration
epochs, ownership errors — which exists because dozens of plugins collided over one
surface. Four panels have not collided.

These are text assertions over TypeScript, which is the shape a Python gate can take.
What they cannot answer — does it compile — `tests/test_frontend_typecheck.py` answers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCIENCE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "science"


def _read(name: str) -> str:
    return (SCIENCE / name).read_text(encoding="utf-8")


def test_the_view_places_slots_rather_than_named_panels():
    """The property that removes the fork.

    A view that names `GoalCard` can only ever render the panels this repository knows
    about; one that renders a slot can render whatever registered into it.
    """
    view = _read("Conversation.tsx")
    for panel in ("GoalCard", "PlanBar"):
        assert f"<{panel}" not in view, f"Conversation.tsx still places {panel} directly"
    assert view.count("<Slot ") >= 2


@pytest.mark.parametrize("slot", ["conversation.above-thread", "conversation.below-thread"])
def test_every_declared_slot_is_both_rendered_and_filled(slot):
    """A slot nothing renders swallows its registrations; a slot nothing fills is a name
    with no meaning. Either alone reads as working."""
    assert slot in _read("Conversation.tsx"), f"{slot} is declared but never rendered"
    assert slot in _read("panels.ts"), f"{slot} is rendered but nothing registers into it"


def test_the_shipped_panels_still_reach_the_view():
    """The rewiring must not have quietly dropped one.

    A panel that stops rendering leaves no error — the slot simply comes back empty —
    which is exactly the failure a registry makes possible and a direct import did not.
    """
    registrations = _read("panels.ts")
    for panel in ("GoalCard", "PlanBar"):
        assert re.search(rf"registerSlot\([^)]*{panel}\)", registrations), (
            f"{panel} is no longer registered into any slot"
        )


def test_the_registrations_are_imported_for_effect():
    """Without a reference, a bundler drops the module and the registrations with it —
    and the failure is an empty conversation view, not a build error."""
    assert "import './panels';" in _read("Conversation.tsx")


def test_a_slot_name_outside_the_union_cannot_be_registered():
    """A typo must fail at compile time rather than register into a slot nobody renders.

    Pinned as a literal union rather than `string`: `registerSlot('conversaton.…', …)`
    would otherwise be accepted, and the panel would simply never appear.
    """
    assert re.search(r"export type SlotName =\s*'[^']+'(\s*\|\s*'[^']+')*;", _read("slots.ts"))


def test_a_disposer_removes_its_own_registration():
    """A reload registers the replacement before disposing the old handle. Removing by
    id would then have the old cleanup delete the new panel."""
    source = _read("slots.ts")
    body = source[source.index("export function registerSlot") :]
    assert "e !== entry" in body, (
        "the disposer filters by id rather than by identity, so an old handle can delete "
        "a newer registration"
    )
