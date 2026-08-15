"""The browser's idea of the wire protocol and the server's are the same idea.

They used to be two ideas that a test compared. `agentevolver/gateway/types.py` defined the
models and `frontend/src/controllers/gateway.ts` declared them again by hand, in another
language, in another build, with neither compiler able to see the other. Both directions of
drift were silent. A field the client declares and the server does not send arrives as
`undefined` while TypeScript stays satisfied, so the break surfaces somewhere else — a
blank panel, or a comparison that quietly takes the wrong branch. A field the server sends
and the client does not declare is a capability nobody can use until someone notices it
exists; three had been shipping for months when this file first looked.

Comparing the copies was the wrong fix, and it failed in the way that fix always fails: a
gate over registered copies only guards the copies it was told about. There was a second
mirror in `frontend/src/cli/protocol.ts` the whole time, and nothing checked it.

So there is one declaration now. `gateway/typescript.py` renders the models, the render is
checked in, and these tests fail while the two differ. The interesting assertions are no
longer "do the field lists match" — they cannot diverge — but "can a hand-written mirror
come back", and "does a new server field actually reach the client".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import pytest
from pydantic import BaseModel, Field

from agentevolver.gateway import types as wire
from agentevolver.gateway.types import PROTOCOL_VERSION
from agentevolver.gateway.typescript import (
    MODELS,
    artifact_path,
    render_typescript,
    write_typescript,
)

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"

#: A hand-written declaration of a wire model. The generated artifact is exempt because it
#: is the one place these may be declared.
_WIRE_INTERFACE = re.compile(r"export interface (Gateway(?:Command|Error|Event|Response))\b")


# --------------------------------------------------------------------------- #
# The artifact is current
# --------------------------------------------------------------------------- #
def test_the_checked_in_typescript_is_what_the_models_render():
    """The gate the whole arrangement rests on.

    Without it the generator is a suggestion: someone edits the Python, forgets to
    regenerate, and the client is back to describing a protocol the server no longer
    speaks — with the added insult that the file says it was generated.
    """
    path = artifact_path(ROOT)
    assert path.exists(), f"{path} is missing; run write_typescript()"
    assert path.read_text(encoding="utf-8") == render_typescript(), (
        "frontend/src/protocol/gateway.ts is stale. Regenerate it with:\n"
        '  python -c "from agentevolver.gateway.typescript import write_typescript; '
        'write_typescript()"'
    )


def test_regenerating_an_unchanged_artifact_reports_no_change(tmp_path):
    """Writing must be idempotent, or the gate above would fight every commit."""
    (tmp_path / "frontend" / "src" / "protocol").mkdir(parents=True)
    assert write_typescript(tmp_path) is True       # first write creates it
    assert write_typescript(tmp_path) is False      # second finds it already correct


# --------------------------------------------------------------------------- #
# A second mirror cannot come back
# --------------------------------------------------------------------------- #
def test_no_hand_written_file_declares_a_wire_type():
    """The defect that made the old gate useless was an unregistered copy.

    A gate that compares one hand-written mirror against the server says nothing about the
    second one, and the second one is the one that drifts — nobody is looking at it. This
    asserts the shape of the arrangement rather than the contents of a list, so a third
    mirror fails here on the day it is written.
    """
    generated = artifact_path(ROOT)
    offenders = [
        f"{path.relative_to(ROOT)}: {', '.join(found)}"
        for path in FRONTEND.rglob("*.ts")
        if path != generated
        for found in [_WIRE_INTERFACE.findall(path.read_text(encoding="utf-8"))]
        if found
    ]
    assert not offenders, (
        "these declare wire types by hand; import them from protocol/gateway instead:\n"
        + "\n".join(offenders)
    )


def test_the_clients_reach_the_protocol_through_the_generated_file():
    """Both clients import it, so neither can be quietly left behind on a rename."""
    for relative in ("controllers/gateway.ts", "cli/protocol.ts"):
        source = (FRONTEND / relative).read_text(encoding="utf-8")
        assert "protocol/gateway" in source, f"{relative} does not import the contract"


# --------------------------------------------------------------------------- #
# What the renderer promises
# --------------------------------------------------------------------------- #
def test_every_wire_model_is_rendered():
    """A model added to the server but not to `MODELS` would never reach a client.

    Read off the module rather than from a second list here, because a second list is the
    thing this whole file exists to stop.
    """
    defined = {
        value.__name__
        for value in vars(wire).values()
        if isinstance(value, type) and issubclass(value, BaseModel) and value is not BaseModel
    }
    assert defined == {model.__name__ for model in MODELS}


def test_a_new_server_field_reaches_the_client():
    """The property that makes this a generator rather than a third copy."""

    class _Added(BaseModel):
        existing: str
        newly_added: int

    from agentevolver.gateway.typescript import _render_model

    rendered = _render_model(_Added)
    assert "newly_added: number;" in rendered


@pytest.mark.parametrize(
    "annotation, expected",
    [
        (str, "value: string;"),
        (int, "value: number;"),
        (bool, "value: boolean;"),
        (Dict[str, Any], "value: Record<string, unknown>;"),
        (List[str], "value: string[];"),
        (Optional[str], "value?: string;"),
        (Literal["event"], "value: 'event';"),
    ],
)
def test_python_shapes_become_the_typescript_a_client_can_use(annotation, expected):
    from agentevolver.gateway.typescript import _render_model

    model = type("_Shape", (BaseModel,), {"__annotations__": {"value": annotation}})
    if expected.startswith("value?"):
        model = type("_Shape", (BaseModel,), {"__annotations__": {"value": annotation},
                                              "value": None})
    assert expected in _render_model(model)


def test_an_optional_field_is_an_optional_key_and_not_a_nullable_one():
    """The server omits these. A client made to handle both an absent key and an explicit
    null has been given two ways to say one thing, and will eventually check only one."""
    rendered = render_typescript()
    assert "conversation_id?: string;" in rendered
    assert "conversation_id: string | null" not in rendered


def test_a_field_description_travels_into_the_generated_comment():
    """The reason a field exists has one home, in the Python model, and reaches the client
    from there. Written twice it would be corrected once.

    Compared with the comment prefixes and wrapping removed: where the renderer breaks a
    line is not a promise, and asserting on it would make rewording a description fail
    here for no reason.
    """
    unwrapped = " ".join(
        line.strip().lstrip("/").strip()
        for line in render_typescript().splitlines()
        if line.strip().startswith("//")
    )
    description = wire.GatewayEvent.model_fields["conversation_id"].description
    assert description in unwrapped


def test_the_discriminator_is_rendered_as_a_literal_so_the_union_narrows():
    """`isGatewayEvent` is a type guard, which only works if `kind` is the literal rather
    than `string` — otherwise every consumer needs a cast."""
    rendered = render_typescript()
    assert "kind: 'event';" in rendered
    assert "kind: 'response';" in rendered


def test_both_sides_claim_the_same_protocol_version():
    assert f"export const PROTOCOL_VERSION = {PROTOCOL_VERSION};" in render_typescript()


def test_the_two_axes_on_an_event_are_both_declared():
    """`kind` says which envelope this is; `type` says which event it carries. They are
    different questions, and an event needs both answered — collapsing them would leave a
    client unable to tell a response from an event, or one event from another."""
    rendered = render_typescript()
    event = rendered[rendered.index("export interface GatewayEvent"):]
    assert "kind: 'event';" in event
    assert "type: string;" in event
