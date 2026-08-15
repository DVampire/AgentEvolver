"""The docs browser serves an index, and refuses everything the index did not offer.

`docs.get` reads files out of the checkout and hands them to whoever reached the gateway
socket. Containment under the repository root is the obvious guard and it is not enough on
its own: the repository root contains every source file, every config, and any `.env` sitting
beside them. So the pair is built as a document browser rather than a file reader — the
listing is an allowlist, and a path that listing never offered is refused even when it is a
perfectly ordinary file inside the repo.

The index is also the docs browser's only navigation. Section order is curated, a section's
`README.md` leads it, and a document is titled by its own first heading rather than by its
filename — `docs/decisions/` holds dated slugs, and a sidebar of those is unreadable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agentevolver.gateway.protocol import PROTOCOL_VERSION, GatewayCommand
from agentevolver.gateway.service import AgentGateway

_REPO_ROOT = Path(__file__).resolve().parents[1]


def call(method: str, params: dict | None = None):
    """Drive one command through the real gateway, as a client would."""

    async def run():
        gateway = AgentGateway()
        return await gateway.handle(GatewayCommand(
            id="t", method=method, params=params or {}, protocol_version=PROTOCOL_VERSION,
        ))

    return asyncio.run(run())


@pytest.fixture(scope="module")
def index() -> list[dict]:
    response = call("docs.list")
    assert response.ok
    return response.result["documents"]


# --------------------------------------------------------------------------- #
# The index
# --------------------------------------------------------------------------- #
def test_the_index_offers_the_documents_a_reader_starts_from(index: list[dict]):
    """A browser whose index is missing the entry points is a browser nobody uses."""
    offered = {entry["path"] for entry in index}

    assert {"README.md", "PROJECT.md", "tests/README.md", "docs/DOC-STANDARD.md"} <= offered


def test_decision_records_and_postmortems_are_reachable(index: list[dict]):
    """These two tiers are the reason the browser exists: they are the documentation that
    is not discoverable from the code, so a reader has to be able to find them without
    knowing the directory names."""
    sections = {entry["section"] for entry in index}

    assert {"Decisions", "Postmortems"} <= sections


def test_a_document_is_titled_by_its_own_first_heading(index: list[dict]):
    """Titles come from the file, not the filename.

    `docs/decisions/` names are dated slugs by convention, so deriving a title from the path
    would fill the sidebar with `2026-08-15-coverage-gate-is-a-dark-file-register`. The
    author already wrote a better one on line 1.
    """
    entry = next(item for item in index if item["path"].endswith("coverage-gate-is-a-dark-file-register.md"))

    assert entry["title"] == "The coverage gate is a dark-file register, not a percentage"


def test_a_section_readme_leads_its_section(index: list[dict]):
    """A section's README explains what the other files in it are, so it goes first.

    Plain alphabetical order buries it: every sibling in `docs/decisions/` starts with a
    date, and `README.md` sorts after all of them.
    """
    decisions = [entry["path"] for entry in index if entry["section"] == "Decisions"]

    assert decisions[0] == "docs/decisions/README.md"


def test_no_document_is_offered_twice(index: list[dict]):
    """`docs/*.md` re-catches files the curated sections named explicitly.

    Without dedup, `docs/DOC-STANDARD.md` appears under both "Working here" and "Reference",
    and clicking one of them highlights neither.
    """
    paths = [entry["path"] for entry in index]

    assert len(paths) == len(set(paths))


def test_module_readmes_are_offered_as_their_own_section(index: list[dict]):
    """The per-module README is the module's contract, and it is the documentation a
    reader most often wants next after the overview."""
    modules = [entry for entry in index if entry["section"] == "Modules"]

    assert len(modules) > 20
    assert all(entry["path"].endswith("/README.md") for entry in modules)


# --------------------------------------------------------------------------- #
# What the browser will not open
# --------------------------------------------------------------------------- #
def test_a_listed_document_comes_back_with_its_content(index: list[dict]):
    """The green path, so a browser that refuses everything cannot pass these tests."""
    response = call("docs.get", {"path": "docs/DOC-STANDARD.md"})

    assert response.ok
    assert response.result["title"] == "Documentation standard"
    assert "One home per fact" in response.result["content"]


@pytest.mark.parametrize("path,why", [
    ("../../../etc/passwd", "climbs out of the checkout"),
    ("docs/../pyproject.toml", "climbs out and back in"),
    ("/etc/passwd", "is absolute"),
])
def test_a_path_that_leaves_the_repository_is_refused(path: str, why: str):
    """Traversal is refused before anything is read, however it is spelled."""
    response = call("docs.get", {"path": path})

    assert not response.ok, f"{path} ({why}) was served"


@pytest.mark.parametrize("path", [
    "pyproject.toml",
    "agentevolver/config/config.py",
    "agentevolver/gateway/service.py",
])
def test_an_unlisted_file_inside_the_repository_is_refused(path: str):
    """The guard that containment alone would not give.

    Each of these sits under the repository root and would pass a `relative_to` check. They
    are refused because the index never offered them — which is what makes this a document
    browser rather than a way to read the checkout over a websocket.
    """
    assert (_REPO_ROOT / path).is_file(), f"{path} should exist for this test to mean anything"

    response = call("docs.get", {"path": path})

    assert not response.ok
    assert "Unknown document" in (response.error.message if response.error else "")


def test_asking_for_nothing_is_refused_rather_than_defaulted():
    """An empty path is a client bug; answering it with some document hides that."""
    assert not call("docs.get", {"path": ""}).ok
    assert not call("docs.get", {}).ok
