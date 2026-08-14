"""Everything a model can pass to a knowledge base, and what it is allowed to become.

Both knowledge operations are canvas nodes, so their arguments come off a model's tool
call: ``documents`` may arrive as a list, a JSON string, a bare sentence, or a
``{"records": [...]}`` wrapper from the node upstream. ``_coerce_documents`` is the only
thing between that and the corpus file, and its mistakes are permanent — a JSON array
stored as one blob is a single giant record that answers every query, and a document
whose ``text`` is ``None`` breaks the ranker on read, long after the ingest that caused
it reported success.

The base name comes from the same untrusted place and is turned into a directory. It has
to land exactly one level below the knowledge root: a name of ``..`` once wrote a corpus
into the root's parent, and the character class allows ``.`` deliberately, so that case
is not hypothetical.

Ranking itself is stubbed here. The backends return positions, so every assertion is
about the plumbing — reading, writing, dispatch, and what happens when a ranker fails.
"""

import json

import pytest

from agentevolver.knowledge.server import KnowledgeManager, _coerce_documents
from agentevolver.knowledge.types import RagBackend


class FirstNBackend(RagBackend):
    """Ranks by position — deterministic, so assertions are about plumbing."""

    name: str = "bm25"

    async def search(self, query, texts, top_k):
        return [(index, 1.0 - index / 100) for index in range(min(top_k, len(texts)))]


class BrokenBackend(RagBackend):
    """Raises on every search, standing in for a corrupt or half-built index."""

    name: str = "broken"

    async def search(self, query, texts, top_k):
        raise RuntimeError("index corrupt")


@pytest.fixture
def knowledge(tmp_path):
    """A manager writing under ``tmp_path``, with the two stub rankers installed.

    ``_backends`` is replaced rather than initialized, so no real ranker (and no model
    download) is involved.
    """
    manager = KnowledgeManager(base_dir=str(tmp_path / "knowledge"))
    manager._backends = {"bm25": FirstNBackend(), "broken": BrokenBackend()}
    return manager


async def ingest(manager, **payload):
    payload.setdefault("base", "notes")
    return await manager("knowledge_ingest", payload)


# --------------------------------------------------------------------------- #
# Turning whatever arrived into documents
# --------------------------------------------------------------------------- #
def test_a_bare_sentence_becomes_one_document():
    assert _coerce_documents("just some text", None, "text") == [{"text": "just some text"}]


def test_a_json_array_string_is_parsed_rather_than_stored_whole():
    """A model that serialises its argument must not produce a single blob.

    Models pass ``documents`` as a JSON string about as often as they pass a list. Stored
    unparsed, the whole batch becomes one record — which then scores against every query
    and drowns the base it was added to.
    """
    docs = _coerce_documents('[{"text": "a"}, {"text": "b"}]', None, "text")
    assert [d["text"] for d in docs] == ["a", "b"]


def test_a_string_that_only_looks_like_json_is_kept_as_text():
    """Prose can start with a bracket. Failing the parse must fall back to treating the
    argument as content, not discard it — the alternative is a silently empty ingest."""
    assert _coerce_documents("[not really json", None, "text") == [{"text": "[not really json"}]


def test_a_records_wrapper_is_unwrapped():
    """``{"records": [...]}`` is the envelope every node in this repo returns, so it is
    what a caller copying a previous result will hand over."""
    assert _coerce_documents({"records": [{"text": "a"}]}, None, "text") == [{"text": "a"}]


def test_records_are_taken_from_upstream_data_when_documents_is_absent():
    """Canvas wires one node's ``data`` into the next node's input.

    A retrieve node feeding an ingest node passes nothing under ``documents`` at all;
    without this fallback the chained ingest writes an empty corpus and reports success.
    """
    assert _coerce_documents(None, {"records": [{"text": "a"}]}, "text") == [{"text": "a"}]


def test_a_custom_text_field_is_projected_onto_text():
    """``text`` is what the ranker reads, so a caller whose records call it ``body`` gets
    a copy under the canonical name rather than a corpus of empty strings."""
    docs = _coerce_documents([{"body": "hello", "id": 1}], None, "body")
    assert docs == [{"body": "hello", "id": 1, "text": "hello"}]


def test_a_document_missing_its_text_field_becomes_empty_not_none():
    """``None`` would break every ranker downstream.

    The failure lands on retrieval, not ingestion — the document is already on disk by
    then, and the traceback points at the ranker rather than at the record that caused it.
    """
    assert _coerce_documents([{"id": 1}], None, "text") == [{"id": 1, "text": ""}]


def test_a_non_string_text_value_is_stringified():
    """Nothing constrains what a model puts under ``text``; the corpus is read back as
    strings regardless."""
    assert _coerce_documents([{"text": 42}], None, "text") == [{"text": "42"}]


def test_extra_fields_survive_ingestion():
    """Retrieval hands whole records back, so a URL or an id attached at ingest time is
    what makes a hit citable. Projecting down to ``text`` alone would lose it."""
    assert _coerce_documents([{"text": "a", "url": "u"}], None, "text")[0]["url"] == "u"


@pytest.mark.parametrize("value", [None, 42, {"no": "records"}])
def test_an_uncoercible_value_yields_nothing_rather_than_raising(value):
    """A bad argument should make an ingest that adds nothing, not a node that throws:
    the canvas step reports "0 added", which is diagnosable, instead of a stack trace."""
    assert _coerce_documents(value, None, "text") == []


def test_items_that_are_neither_strings_nor_records_are_dropped():
    """A ragged list is normal output from a model. The usable entries are kept and the
    rest skipped, so one stray ``None`` does not cost the whole batch."""
    assert _coerce_documents(["a", 42, None, {"text": "b"}], None, "text") == [
        {"text": "a"}, {"text": "b"},
    ]


# --------------------------------------------------------------------------- #
# Where a base name is allowed to point
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "base",
    ["../escape", "/etc/passwd", "a/b", "  ", "a b", "..", ".", "....", "../..", "./."],
)
def test_a_base_name_cannot_point_outside_the_knowledge_root(knowledge, base):
    """The name arrives from a model; it must stay one directory below the root.

    ``..`` and ``.`` matter specially: the character class allows ``.`` so
    version-ish names survive, which once let those two through intact — ``..``
    put a corpus a level above the knowledge root and ``.`` wrote into the root
    itself.
    """
    from pathlib import Path

    resolved = Path(knowledge._base_path(base)).resolve()
    root = Path(knowledge.base_dir).resolve()
    assert resolved.parent == root
    assert resolved != root


@pytest.mark.parametrize("base", ["", "..", ".", "  ", "///"])
def test_a_base_name_with_nothing_usable_falls_back_to_a_default(knowledge, base):
    """Sanitising these leaves an empty string, and an empty name would resolve to the
    root directory itself — so there has to be a name to land on instead."""
    assert knowledge._base_path(base).endswith("default")


def test_a_dot_inside_a_name_is_kept(knowledge):
    """Only the ends are stripped — ``v1.0`` is a reasonable base name."""
    assert knowledge._base_path("corpus.v1.0").endswith("corpus.v1.0")


@pytest.mark.asyncio
async def test_a_base_named_dot_dot_still_stores_and_retrieves(knowledge):
    """Falling back must leave a working base, not a half-broken one.

    Ingest and retrieve sanitise the name independently. If they disagreed, the write
    would succeed and the read would report an empty base — the confusing failure, since
    nothing in the ingest response hints that the name was rewritten.
    """
    await ingest(knowledge, base="..", documents=["alpha"])
    result = await knowledge("knowledge_retrieve", {"base": "..", "query": "a"})
    assert result.success is True
    assert result.data["records"][0]["text"] == "alpha"


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ingested_documents_are_readable_back(knowledge):
    """``added`` and ``total`` are the only feedback a caller gets, so they have to match
    what is actually on disk rather than what was handed in."""
    result = await ingest(knowledge, documents=["alpha", "beta"])
    assert result.success is True
    assert result.data["added"] == 2
    assert result.data["total"] == 2
    assert [d["text"] for d in knowledge._read_corpus("notes")] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_ingesting_again_appends_rather_than_replaces(knowledge):
    """A base is built up over many calls. Truncating on write would leave every base
    holding only its last batch, and nothing in the response would say so."""
    await ingest(knowledge, documents=["alpha"])
    second = await ingest(knowledge, documents=["beta"])
    assert second.data["total"] == 2


@pytest.mark.asyncio
async def test_two_bases_do_not_see_each_other(knowledge):
    """Separate bases are the whole point of naming them — one shared corpus file would
    make every retrieval return other tasks' documents."""
    await ingest(knowledge, base="a", documents=["from a"])
    await ingest(knowledge, base="b", documents=["from b"])
    assert [d["text"] for d in knowledge._read_corpus("a")] == ["from a"]
    assert [d["text"] for d in knowledge._read_corpus("b")] == ["from b"]


@pytest.mark.asyncio
async def test_ingest_without_a_base_is_refused(knowledge):
    """There is a fallback name for an unusable base, so defaulting a missing one would
    be easy and wrong: documents would pile up in ``default`` and the caller would go on
    querying the base they meant."""
    result = await knowledge("knowledge_ingest", {"documents": ["x"]})
    assert result.success is False
    assert "'base' is required" in result.message


@pytest.mark.asyncio
async def test_an_unknown_ranker_names_the_ones_that_exist(knowledge):
    """The ranker is chosen by a model from a string. Listing what is installed turns a
    wrong guess into a correctable one instead of a repeated one."""
    result = await ingest(knowledge, documents=["x"], type="nonesuch")
    assert result.success is False
    assert "bm25" in result.message


@pytest.mark.asyncio
async def test_the_chosen_ranker_is_recorded_with_the_base(knowledge):
    """Retrieval never takes a ranker argument — it reads the one the base was built
    with. Without the manifest, a corpus indexed one way would be searched another."""
    await ingest(knowledge, documents=["x"], type="broken")
    assert knowledge._read_type("notes") == "broken"


@pytest.mark.asyncio
async def test_a_base_without_a_manifest_falls_back_to_the_default_ranker(knowledge):
    assert knowledge._read_type("never-ingested") == "bm25"


@pytest.mark.asyncio
async def test_a_corrupt_manifest_falls_back_rather_than_raising(knowledge):
    """The manifest is rewritten on every ingest, so an interrupted run leaves this.

    A corpus with a truncated manifest is still perfectly searchable; refusing to read it
    would strand the documents over a file that holds one key.
    """
    import os

    await ingest(knowledge, documents=["x"])
    with open(os.path.join(knowledge._base_path("notes"), "manifest.json"), "w") as fh:
        fh.write("{ broken")
    assert knowledge._read_type("notes") == "bm25"


@pytest.mark.asyncio
async def test_unicode_survives_the_corpus_round_trip(knowledge):
    """The corpus is JSONL. Written with the default ASCII escaping and read back without
    an explicit encoding, non-Latin text survives on some machines and not others."""
    await ingest(knowledge, documents=["中文内容"])
    assert knowledge._read_corpus("notes")[0]["text"] == "中文内容"


# --------------------------------------------------------------------------- #
# Retrieve
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_retrieval_returns_scored_records(knowledge):
    """The stub ranks by position, so the order is fixed and the assertion is about what
    the manager does with the hits: keep whole records, attach the score, honour ``top_k``."""
    await ingest(knowledge, documents=["alpha", "beta", "gamma"])
    result = await knowledge("knowledge_retrieve", {"base": "notes", "query": "a", "top_k": 2})
    assert result.success is True
    assert result.data["count"] == 2
    assert [r["text"] for r in result.data["records"]] == ["alpha", "beta"]
    assert all("score" in r for r in result.data["records"])


@pytest.mark.asyncio
async def test_the_query_may_arrive_as_a_task_from_an_upstream_node(knowledge):
    """A retrieve node wired straight off a task carries it under ``task``, not ``query``;
    without the alias that wiring fails as "'query' is required" and looks like a bug in
    the canvas rather than a name mismatch."""
    await ingest(knowledge, documents=["alpha"])
    result = await knowledge("knowledge_retrieve", {"base": "notes", "task": "find alpha"})
    assert result.success is True


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, expected", [
    ({"query": "x"}, "'base' is required"),
    ({"base": "notes"}, "'query' is required"),
])
async def test_retrieval_states_which_argument_is_missing(knowledge, payload, expected):
    """The message goes back to the model that made the call, and is all it has to work
    from; "invalid arguments" would leave it guessing which one."""
    assert expected in (await knowledge("knowledge_retrieve", payload)).message


@pytest.mark.asyncio
async def test_retrieving_from_an_empty_base_says_so(knowledge):
    """An empty base is almost always a mistyped name or an ingest that never ran.

    Returning zero hits successfully would read as "nothing matched" and send the caller
    off rewriting the query for a corpus that does not exist.
    """
    result = await knowledge("knowledge_retrieve", {"base": "never", "query": "x"})
    assert result.success is False
    assert "empty or missing" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("top_k", ["not-a-number", None, 0, -5])
async def test_a_nonsense_top_k_falls_back_to_something_workable(knowledge, top_k):
    """The argument comes from a model; it must not be able to return nothing.

    ``0`` and ``-5`` are the dangerous ones: they are valid integers, so they pass any
    type check and produce an empty, successful result that reads as "no matches".
    """
    await ingest(knowledge, documents=["a", "b", "c", "d", "e"])
    result = await knowledge("knowledge_retrieve", {"base": "notes", "query": "x", "top_k": top_k})
    assert result.success is True
    assert result.data["count"] >= 1


@pytest.mark.asyncio
async def test_a_ranker_that_fails_becomes_a_failed_result_not_an_exception(knowledge):
    """A third-party ranker can raise anything. An exception escaping here kills the
    canvas step; a failed Response carrying the ranker's own message lets the agent see
    what went wrong and move on."""
    await ingest(knowledge, documents=["a"], type="broken")
    result = await knowledge("knowledge_retrieve", {"base": "notes", "query": "x"})
    assert result.success is False
    assert "index corrupt" in result.message


@pytest.mark.asyncio
async def test_a_base_recorded_against_a_missing_ranker_is_reported(knowledge):
    """Bases outlive the process that built them, so the ranker named in the manifest may
    simply not be installed any more. That is a configuration problem, and saying so
    beats a ``None`` backend failing an attribute lookup one frame down."""
    await ingest(knowledge, documents=["a"], type="broken")
    del knowledge._backends["broken"]
    result = await knowledge("knowledge_retrieve", {"base": "notes", "query": "x"})
    assert result.success is False
    assert "unavailable" in result.message


# --------------------------------------------------------------------------- #
# What the manager advertises about itself
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_unknown_operation_is_refused_by_name(knowledge):
    """The operation name is dispatched on a string. Echoing the one that missed is what
    distinguishes "this node does not exist" from "this node failed"."""
    result = await knowledge("knowledge_delete", {})
    assert result.success is False
    assert "knowledge_delete" in result.message


@pytest.mark.asyncio
async def test_the_callable_operations_are_listed_for_step_validation(knowledge):
    """A canvas step is validated against this list before it runs, so an operation
    missing from it is unreachable however well it is implemented."""
    assert set(await knowledge.list()) == {"knowledge_ingest", "knowledge_retrieve"}


@pytest.mark.asyncio
async def test_the_available_rankers_are_listed_for_the_node_parameter(knowledge):
    """This is what fills the node's ``type`` parameter — a ranker absent from it can be
    installed and still never chosen."""
    assert set(await knowledge.list_types()) == {"bm25", "broken"}


@pytest.mark.asyncio
async def test_operation_info_is_available_for_known_names_only(knowledge):
    """The description is what a model reads to decide whether to call the node; an
    unknown name has to come back as ``None`` rather than an empty record."""
    assert (await knowledge.get_info("knowledge_ingest")).description
    assert await knowledge.get_info("nonsense") is None


@pytest.mark.asyncio
async def test_cleanup_drops_the_backends(knowledge):
    """Rankers can hold loaded models; teardown that left them referenced would keep that
    memory for the life of the process."""
    await knowledge.cleanup()
    assert await knowledge.list_types() == []
