"""Knowledge: normalising whatever an agent passes, and keeping bases apart.

Both operations are canvas nodes, so their arguments arrive from a model and can
be almost anything — a JSON string, a bare sentence, a records wrapper. The
coercion in front of ingest is what stops that from becoming a corrupt corpus.
The other half is containment: a base name comes from the same untrusted place
and is turned into a directory, so it must not be able to point outside the
knowledge root.
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
    name: str = "broken"

    async def search(self, query, texts, top_k):
        raise RuntimeError("index corrupt")


@pytest.fixture
def knowledge(tmp_path):
    manager = KnowledgeManager(base_dir=str(tmp_path / "knowledge"))
    manager._backends = {"bm25": FirstNBackend(), "broken": BrokenBackend()}
    return manager


async def ingest(manager, **payload):
    payload.setdefault("base", "notes")
    return await manager("knowledge_ingest", payload)


# ------------------------------------------------------------------ coercion
def test_a_bare_sentence_becomes_one_document():
    assert _coerce_documents("just some text", None, "text") == [{"text": "just some text"}]


def test_a_json_array_string_is_parsed_rather_than_stored_whole():
    """A model that serialises its argument must not produce a single blob."""
    docs = _coerce_documents('[{"text": "a"}, {"text": "b"}]', None, "text")
    assert [d["text"] for d in docs] == ["a", "b"]


def test_a_string_that_only_looks_like_json_is_kept_as_text():
    assert _coerce_documents("[not really json", None, "text") == [{"text": "[not really json"}]


def test_a_records_wrapper_is_unwrapped():
    assert _coerce_documents({"records": [{"text": "a"}]}, None, "text") == [{"text": "a"}]


def test_records_are_taken_from_upstream_data_when_documents_is_absent():
    """Canvas wires one node's ``data`` into the next node's input."""
    assert _coerce_documents(None, {"records": [{"text": "a"}]}, "text") == [{"text": "a"}]


def test_a_custom_text_field_is_projected_onto_text():
    docs = _coerce_documents([{"body": "hello", "id": 1}], None, "body")
    assert docs == [{"body": "hello", "id": 1, "text": "hello"}]


def test_a_document_missing_its_text_field_becomes_empty_not_none():
    """``None`` would break every ranker downstream."""
    assert _coerce_documents([{"id": 1}], None, "text") == [{"id": 1, "text": ""}]


def test_a_non_string_text_value_is_stringified():
    assert _coerce_documents([{"text": 42}], None, "text") == [{"text": "42"}]


def test_extra_fields_survive_ingestion():
    assert _coerce_documents([{"text": "a", "url": "u"}], None, "text")[0]["url"] == "u"


@pytest.mark.parametrize("value", [None, 42, {"no": "records"}])
def test_an_uncoercible_value_yields_nothing_rather_than_raising(value):
    assert _coerce_documents(value, None, "text") == []


def test_items_that_are_neither_strings_nor_records_are_dropped():
    assert _coerce_documents(["a", 42, None, {"text": "b"}], None, "text") == [
        {"text": "a"}, {"text": "b"},
    ]


# ------------------------------------------------------------------- naming
@pytest.mark.parametrize(
    "base",
    ["../escape", "/etc/passwd", "a/b", "  ", "a b", "..", ".", "....", "../..", "./."],
)
def test_a_base_name_cannot_point_outside_the_knowledge_root(knowledge, base):
    """The name arrives from a model; it must stay one directory below the root.

    ``..`` and ``.`` matter specially: the character class allows ``.`` so
    version-ish names survive, which once let those two through intact.
    """
    from pathlib import Path

    resolved = Path(knowledge._base_path(base)).resolve()
    root = Path(knowledge.base_dir).resolve()
    assert resolved.parent == root
    assert resolved != root


@pytest.mark.parametrize("base", ["", "..", ".", "  ", "///"])
def test_a_base_name_with_nothing_usable_falls_back_to_a_default(knowledge, base):
    assert knowledge._base_path(base).endswith("default")


def test_a_dot_inside_a_name_is_kept(knowledge):
    """Only the ends are stripped — ``v1.0`` is a reasonable base name."""
    assert knowledge._base_path("corpus.v1.0").endswith("corpus.v1.0")


@pytest.mark.asyncio
async def test_a_base_named_dot_dot_still_stores_and_retrieves(knowledge):
    """Falling back must leave a working base, not a half-broken one."""
    await ingest(knowledge, base="..", documents=["alpha"])
    result = await knowledge("knowledge_retrieve", {"base": "..", "query": "a"})
    assert result.success is True
    assert result.data["records"][0]["text"] == "alpha"


# ------------------------------------------------------------------- ingest
@pytest.mark.asyncio
async def test_ingested_documents_are_readable_back(knowledge):
    result = await ingest(knowledge, documents=["alpha", "beta"])
    assert result.success is True
    assert result.data["added"] == 2
    assert result.data["total"] == 2
    assert [d["text"] for d in knowledge._read_corpus("notes")] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_ingesting_again_appends_rather_than_replaces(knowledge):
    await ingest(knowledge, documents=["alpha"])
    second = await ingest(knowledge, documents=["beta"])
    assert second.data["total"] == 2


@pytest.mark.asyncio
async def test_two_bases_do_not_see_each_other(knowledge):
    await ingest(knowledge, base="a", documents=["from a"])
    await ingest(knowledge, base="b", documents=["from b"])
    assert [d["text"] for d in knowledge._read_corpus("a")] == ["from a"]
    assert [d["text"] for d in knowledge._read_corpus("b")] == ["from b"]


@pytest.mark.asyncio
async def test_ingest_without_a_base_is_refused(knowledge):
    result = await knowledge("knowledge_ingest", {"documents": ["x"]})
    assert result.success is False
    assert "'base' is required" in result.message


@pytest.mark.asyncio
async def test_an_unknown_ranker_names_the_ones_that_exist(knowledge):
    result = await ingest(knowledge, documents=["x"], type="nonesuch")
    assert result.success is False
    assert "bm25" in result.message


@pytest.mark.asyncio
async def test_the_chosen_ranker_is_recorded_with_the_base(knowledge):
    await ingest(knowledge, documents=["x"], type="broken")
    assert knowledge._read_type("notes") == "broken"


@pytest.mark.asyncio
async def test_a_base_without_a_manifest_falls_back_to_the_default_ranker(knowledge):
    assert knowledge._read_type("never-ingested") == "bm25"


@pytest.mark.asyncio
async def test_a_corrupt_manifest_falls_back_rather_than_raising(knowledge):
    import os

    await ingest(knowledge, documents=["x"])
    with open(os.path.join(knowledge._base_path("notes"), "manifest.json"), "w") as fh:
        fh.write("{ broken")
    assert knowledge._read_type("notes") == "bm25"


@pytest.mark.asyncio
async def test_unicode_survives_the_corpus_round_trip(knowledge):
    await ingest(knowledge, documents=["中文内容"])
    assert knowledge._read_corpus("notes")[0]["text"] == "中文内容"


# ----------------------------------------------------------------- retrieve
@pytest.mark.asyncio
async def test_retrieval_returns_scored_records(knowledge):
    await ingest(knowledge, documents=["alpha", "beta", "gamma"])
    result = await knowledge("knowledge_retrieve", {"base": "notes", "query": "a", "top_k": 2})
    assert result.success is True
    assert result.data["count"] == 2
    assert [r["text"] for r in result.data["records"]] == ["alpha", "beta"]
    assert all("score" in r for r in result.data["records"])


@pytest.mark.asyncio
async def test_the_query_may_arrive_as_a_task_from_an_upstream_node(knowledge):
    await ingest(knowledge, documents=["alpha"])
    result = await knowledge("knowledge_retrieve", {"base": "notes", "task": "find alpha"})
    assert result.success is True


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, expected", [
    ({"query": "x"}, "'base' is required"),
    ({"base": "notes"}, "'query' is required"),
])
async def test_retrieval_states_which_argument_is_missing(knowledge, payload, expected):
    assert expected in (await knowledge("knowledge_retrieve", payload)).message


@pytest.mark.asyncio
async def test_retrieving_from_an_empty_base_says_so(knowledge):
    result = await knowledge("knowledge_retrieve", {"base": "never", "query": "x"})
    assert result.success is False
    assert "empty or missing" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("top_k", ["not-a-number", None, 0, -5])
async def test_a_nonsense_top_k_falls_back_to_something_workable(knowledge, top_k):
    """The argument comes from a model; it must not be able to return nothing."""
    await ingest(knowledge, documents=["a", "b", "c", "d", "e"])
    result = await knowledge("knowledge_retrieve", {"base": "notes", "query": "x", "top_k": top_k})
    assert result.success is True
    assert result.data["count"] >= 1


@pytest.mark.asyncio
async def test_a_ranker_that_fails_becomes_a_failed_result_not_an_exception(knowledge):
    await ingest(knowledge, documents=["a"], type="broken")
    result = await knowledge("knowledge_retrieve", {"base": "notes", "query": "x"})
    assert result.success is False
    assert "index corrupt" in result.message


@pytest.mark.asyncio
async def test_a_base_recorded_against_a_missing_ranker_is_reported(knowledge):
    await ingest(knowledge, documents=["a"], type="broken")
    del knowledge._backends["broken"]
    result = await knowledge("knowledge_retrieve", {"base": "notes", "query": "x"})
    assert result.success is False
    assert "unavailable" in result.message


# --------------------------------------------------------------- operations
@pytest.mark.asyncio
async def test_an_unknown_operation_is_refused_by_name(knowledge):
    result = await knowledge("knowledge_delete", {})
    assert result.success is False
    assert "knowledge_delete" in result.message


@pytest.mark.asyncio
async def test_the_callable_operations_are_listed_for_step_validation(knowledge):
    assert set(await knowledge.list()) == {"knowledge_ingest", "knowledge_retrieve"}


@pytest.mark.asyncio
async def test_the_available_rankers_are_listed_for_the_node_parameter(knowledge):
    assert set(await knowledge.list_types()) == {"bm25", "broken"}


@pytest.mark.asyncio
async def test_operation_info_is_available_for_known_names_only(knowledge):
    assert (await knowledge.get_info("knowledge_ingest")).description
    assert await knowledge.get_info("nonsense") is None


@pytest.mark.asyncio
async def test_cleanup_drops_the_backends(knowledge):
    await knowledge.cleanup()
    assert await knowledge.list_types() == []
