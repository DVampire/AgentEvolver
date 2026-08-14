"""A symbol question answered by a language server, or refused in a way that says so.

`grep` finds a string. It does not find the definition, the references, or the type — and
those are most of what makes an edit safe. An agent about to rename a method has to know
which of the nine matches is the one it means, and the only thing in this repo that could
tell it was a regex.

The refusal path matters as much as the answer. With no server installed the tool stays in
the roster and returns a structured `LSP_UNAVAILABLE`, rather than disappearing: a tool
that comes and goes changes the prompt, and the prompt sits ahead of the cache breakpoint,
so an absent tool would cost the session's cached prefix to say nothing.
"""

import asyncio
from typing import Dict, List

import pytest

from agentevolver.lsp import lsp_manager
from agentevolver.lsp.types import (Hover, Location, LspError, LspErrorCode, LspOperation,
                                    LspProvider, LspQuery, LspResult, Position, Range,
                                    ResultKind)
from agentevolver.tool.default.lsp import LspTool


class _Ctx:
    id = "lsp_test_session"


class FakeProvider(LspProvider):
    """A provider that speaks the seam's contract without a subprocess.

    A real language server is not installed here and installing one would make the suite
    depend on a network. What the seam owns — routing, position translation, refusals,
    reaping — is all above the wire, so a fake exercises it exactly.
    """

    def __init__(self, provider_id: str = "fake", extensions: Dict[str, str] = None):
        self.id = provider_id
        self.extension_to_language = extensions or {".py": "python"}
        self.seen: List[LspQuery] = []
        self.forgotten: List[str] = []
        self.closed = 0
        self.raises: LspError = None

    def query(self, query: LspQuery) -> LspResult:
        self.seen.append(query)
        if self.raises is not None:
            raise self.raises
        if query.operation is LspOperation.HOVER:
            return LspResult(kind=ResultKind.HOVER,
                             hover=Hover(contents="def answer() -> int"))
        location = Location(uri=f"file://{query.file_path}",
                            range=Range(start=Position(line=7, character=4),
                                        end=Position(line=7, character=10)))
        return LspResult(kind=ResultKind.LOCATIONS, locations=[location])

    def forget(self, session_id: str) -> None:
        self.forgotten.append(session_id)

    def close_all(self) -> None:
        self.closed += 1


@pytest.fixture
def provider():
    """Register a fake for the test, and leave the registry as it was found."""
    fake = FakeProvider()
    # The default-provider probe runs once and looks for an executable on PATH; marking
    # it attempted keeps the test off the machine's installed software either way.
    lsp_manager._default_attempted = True
    lsp_manager.register_provider(fake)
    yield fake
    lsp_manager.unregister_provider(fake.id)


# --------------------------------------------------------------------------- #
# Routing — a question reaches the provider that speaks the language
# --------------------------------------------------------------------------- #
def test_a_query_reaches_the_provider_registered_for_that_extension(provider, tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def answer():\n    return 1\n", encoding="utf-8")

    result = lsp_manager.query(operation=LspOperation.DEFINITION,
                               file_path=str(source), workspace_root=str(tmp_path),
                               position=Position(line=1, character=5))

    assert result.kind is ResultKind.LOCATIONS
    assert provider.seen and provider.seen[0].operation is LspOperation.DEFINITION


def test_the_language_id_comes_from_the_provider_not_the_caller(provider, tmp_path):
    """A caller that could name the language could name one the server does not speak.

    The failure would then arrive as an empty result — which reads to the model as "no
    definition exists" — instead of as a refusal.
    """
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n", encoding="utf-8")

    lsp_manager.query(operation=LspOperation.DEFINITION, file_path=str(source),
                      workspace_root=str(tmp_path), position=Position(line=1, character=1))

    assert provider.seen[-1].language_id == "python"


def test_an_extension_nobody_claims_is_refused_rather_than_guessed(provider, tmp_path):
    """Routing a `.rs` file to a Python server produces confident nonsense."""
    source = tmp_path / "sample.rs"
    source.write_text("fn main() {}\n", encoding="utf-8")

    with pytest.raises(LspError) as raised:
        lsp_manager.query(operation=LspOperation.DEFINITION, file_path=str(source),
                          workspace_root=str(tmp_path),
                          position=Position(line=1, character=1))

    assert raised.value.code is LspErrorCode.UNAVAILABLE


def test_two_providers_cannot_claim_one_extension(provider):
    """Silently letting the second win makes which server answered depend on load order."""
    intruder = FakeProvider(provider_id="other", extensions={".py": "python"})

    with pytest.raises(LspError) as raised:
        lsp_manager.register_provider(intruder)

    assert raised.value.code is LspErrorCode.CONFLICT


# --------------------------------------------------------------------------- #
# The tool the model sees
# --------------------------------------------------------------------------- #
def test_the_tool_declares_itself_as_reading_only():
    """`mutates` and `permission_mode` are what the plan gate reads.

    An LSP query is a read, so declaring it honestly is what lets it through a gate that
    refuses anything undeclared.
    """
    tool = LspTool()
    assert tool.mutates is False
    assert tool.permission_mode == "read_only"


@pytest.mark.asyncio
async def test_an_answer_comes_back_as_readable_text(provider, tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def answer():\n    return 1\n", encoding="utf-8")

    result = await LspTool()(operation="definition", path=str(source),
                             line=1, character=5, ctx=_Ctx())

    assert result.success
    assert "sample.py" in result.message


@pytest.mark.asyncio
async def test_hover_returns_the_type_rather_than_a_location(provider, tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def answer():\n    return 1\n", encoding="utf-8")

    result = await LspTool()(operation="hover", path=str(source),
                             line=1, character=5, ctx=_Ctx())

    assert result.success
    assert "int" in result.message


@pytest.mark.asyncio
async def test_an_unknown_operation_names_the_ones_that_exist():
    """A bare rejection leaves the model guessing at a vocabulary it was never shown."""
    result = await LspTool()(operation="rename", path="x.py", line=1, character=1,
                             ctx=_Ctx())

    assert not result.success
    for operation in ("definition", "references", "hover", "symbols"):
        assert operation in result.message


@pytest.mark.asyncio
async def test_with_no_server_the_tool_stays_and_says_what_to_install(tmp_path):
    """The refusal is the interesting path: it is the state on most machines.

    Removing the tool instead would change the prompt, and the prompt sits ahead of the
    cache breakpoint — an absent tool would cost the session's cached prefix to say
    nothing. The message has to leave the model somewhere to go, or it retries the same
    call.
    """
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n", encoding="utf-8")

    result = await LspTool()(operation="definition", path=str(source),
                             line=1, character=1, ctx=_Ctx())

    if result.success:
        pytest.skip("a language server is installed here; the refusal path is not reachable")
    assert LspErrorCode.UNAVAILABLE.value in result.message
    assert "pip install" in result.message, "the refusal does not say how to fix it"
    assert "grep_search_tool" in result.message, "no fallback offered for right now"


# --------------------------------------------------------------------------- #
# Processes are owned, not leaked
# --------------------------------------------------------------------------- #
def test_a_finished_session_releases_its_language_servers(provider):
    """A language server is a long-lived child, and the run that started it ends.

    `Agent._release_session_resources` knows a session id and nothing about subprocesses,
    which is why `forget` is part of the provider contract rather than an internal detail.
    """
    lsp_manager.forget("some-session")

    assert provider.forgotten == ["some-session"]


def test_closing_everything_reaches_every_provider(provider):
    lsp_manager.close_all()

    assert provider.closed == 1
