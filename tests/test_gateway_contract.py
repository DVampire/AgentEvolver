"""The browser's idea of the wire protocol and the server's are the same idea.

`agentevolver/gateway/protocol.py` defines the wire models; `frontend/src/controllers/
gateway.ts` declares them again by hand, in another language, in another build. Nothing
made the two agree, and neither compiler can see the other.

Both directions of drift are silent and neither is theoretical.

A field the client declares and the server does not send arrives as `undefined`. TypeScript
is satisfied — the type says it is there — and the failure surfaces somewhere else entirely,
as a blank panel or a comparison against `undefined` that quietly takes the wrong branch.
That is what a rename on the server does to a client that was not updated with it.

A field the server sends and the client does not declare is a capability the frontend
cannot use without someone first noticing it exists. This is the same defect this repository
keeps finding in its Python — something computed and handed to nobody — and this file found
three on its first run: `protocol_version` on both responses and events, and `details` on
errors, all sent for months and declared nowhere.

Fields are compared, not types: the shapes are `Dict[str, Any]` on one side and
`Record<string, unknown>` on the other, so a type comparison would be theatre. A missing or
misspelled field is the drift that actually happens.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentevolver.gateway.protocol import (
    PROTOCOL_VERSION,
    GatewayCommand,
    GatewayError,
    GatewayEvent,
    GatewayResponse,
)

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "frontend" / "src" / "controllers" / "gateway.ts"

#: Start of a client wire model. The body is taken by matching braces rather than by
#: regex: `[^}]*` stops at the first `}`, which for `GatewayResponse` is the end of its
#: nested `error?: { … }` — truncating the interface before its remaining fields while
#: pulling the nested ones in. Both errors point the same way, at a mismatch that is not
#: there.
_INTERFACE_START = re.compile(r"export interface (?P<name>\w+)\s*\{")


def _balanced_body(source: str, open_brace: int) -> str:
    """The text between ``open_brace`` and the `}` that closes it."""
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1:index]
    raise AssertionError("unbalanced braces in the client transport")

#: `field?: type` — anchored to a line start OR to a `;`/`{` separator, because the client
#: writes nested shapes inline (`error?: { code: string; message: string }`). Anchoring only
#: to line starts finds `code` there and misses `message`, which reads as a missing field
#: when nothing is missing at all.
_FIELD = re.compile(r"(?:^|[;{])\s*(?P<name>\w+)\??\s*:", re.MULTILINE)


def client_interfaces() -> dict[str, set[str]]:
    """Every `export interface` in the client transport, as name → field names."""
    source = CLIENT.read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}
    for match in _INTERFACE_START.finditer(source):
        body = _balanced_body(source, match.end() - 1)
        # A nested object literal (`error?: { code: string }`) would otherwise contribute
        # its own members as if they were the interface's. Drop anything inside braces.
        flat = re.sub(r"\{[^{}]*\}", "", body)
        found[match.group("name")] = set(_FIELD.findall(flat))
    return found


#: The models both sides speak, paired with the client interface that mirrors each.
PAIRS = [
    ("GatewayResponse", GatewayResponse),
    ("GatewayEvent", GatewayEvent),
]


# --------------------------------------------------------------------------- #
# The two directions of drift
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("interface,model", PAIRS)
def test_the_client_declares_every_field_the_server_sends(interface: str, model):
    """A sent field nobody declared is a capability the frontend cannot reach.

    It is also how a protocol grows a field that no client ever adopts: the server pays to
    compute and send it, and the only record that it exists is the Python model.
    """
    declared = client_interfaces()[interface]
    sent = set(model.model_fields)

    missing = sorted(sent - declared)
    assert not missing, (
        f"{interface} in {CLIENT.relative_to(ROOT)} is missing {missing}, which "
        f"{model.__name__} sends"
    )


@pytest.mark.parametrize("interface,model", PAIRS)
def test_the_client_declares_nothing_the_server_does_not_send(interface: str, model):
    """The dangerous direction: the client reads a field that never arrives.

    TypeScript cannot catch it — the declaration says the field is there — so the value is
    `undefined` at runtime and the failure appears wherever it is used rather than where it
    is wrong. A server-side rename with no client change looks exactly like this.
    """
    declared = client_interfaces()[interface]
    sent = set(model.model_fields)

    invented = sorted(declared - sent)
    assert not invented, (
        f"{interface} in {CLIENT.relative_to(ROOT)} declares {invented}, which "
        f"{model.__name__} never sends — these arrive as undefined"
    )


def test_the_error_shape_carries_everything_an_error_reports():
    """`GatewayError` is nested inside a response rather than declared on its own.

    Checked separately for that reason, and worth checking: `details` is where a refusal
    says *what* was wrong, and a client that cannot see it can only show the message.
    """
    source = CLIENT.read_text(encoding="utf-8")
    nested = re.search(r"error\??\s*:\s*\{(?P<body>[^}]*)\}", source)

    assert nested, "the client's response type no longer declares an `error` member"
    declared = set(_FIELD.findall(nested.group("body")))
    missing = sorted(set(GatewayError.model_fields) - declared)

    assert not missing, f"the client's error shape is missing {missing}"


# --------------------------------------------------------------------------- #
# The version both sides claim to speak
# --------------------------------------------------------------------------- #
def test_both_sides_claim_the_same_protocol_version():
    """The number exists so a mismatch is refused rather than half-executed.

    Two copies of it that disagree is worse than one copy: the server refuses a command the
    client believed was current, and the client's own constant says otherwise.
    """
    source = CLIENT.read_text(encoding="utf-8")
    match = re.search(r"export const PROTOCOL_VERSION\s*=\s*(\d+)", source)

    assert match, "the client no longer exports PROTOCOL_VERSION"
    assert int(match.group(1)) == PROTOCOL_VERSION, (
        f"client speaks protocol {match.group(1)}, server speaks {PROTOCOL_VERSION}"
    )


def test_the_command_the_client_sends_is_the_command_the_server_accepts():
    """`GatewayCommand` forbids extra fields, so an invented one is refused outright.

    The client builds commands inline rather than from an interface, so this pins the four
    keys it constructs against the model that validates them.
    """
    source = CLIENT.read_text(encoding="utf-8")
    sent = set(re.findall(r"^\s*(id|method|params|protocol_version)\s*[,:]", source, re.MULTILINE))

    assert sent <= set(GatewayCommand.model_fields), (
        f"the client sends {sorted(sent - set(GatewayCommand.model_fields))}, which "
        f"GatewayCommand forbids"
    )


# --------------------------------------------------------------------------- #
# The extractor
# --------------------------------------------------------------------------- #
def test_the_parser_finds_the_interfaces_it_is_asked_about():
    """A gate over an empty parse passes forever.

    If the client's declaration style ever changes — a type alias, a generated file — every
    check above silently compares two empty sets and stays green.
    """
    found = client_interfaces()

    for interface, _ in PAIRS:
        assert found.get(interface), f"could not parse {interface} out of the client"


def test_a_nested_object_does_not_leak_its_members_into_the_interface():
    """`error?: { code: string }` must not contribute `code` to the outer interface,
    or the response would appear to declare a field the server never sends at that level."""
    assert "code" not in client_interfaces()["GatewayResponse"]
