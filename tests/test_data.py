"""Data: normalising model-supplied records into something pyarrow will accept.

Both operations are canvas nodes fed by an upstream node's output, so records
arrive as a list, a JSON string, or a ``{records: [...]}`` envelope, and the
records themselves are ragged. Aligning them is not cosmetic — HuggingFace and
pyarrow reject a batch whose rows do not share a schema, and the failure surfaces
far from the node that caused it.

The Hub round trip itself is network-bound and not exercised here.
"""

import pytest

from agentevolver.data.server import (
    DATA_OPERATIONS,
    DataManager,
    _align_records,
    _coerce_records,
    _resolve_token,
)


@pytest.fixture
def data(tmp_path):
    return DataManager(base_dir=str(tmp_path / "datasets"))


# ------------------------------------------------------------------ coercion
def test_a_plain_list_passes_through():
    assert _coerce_records([{"a": 1}], None) == [{"a": 1}]


def test_a_json_string_is_parsed():
    """A model that serialises its argument must not lose the rows."""
    assert _coerce_records('[{"a": 1}]', None) == [{"a": 1}]


def test_a_records_envelope_is_unwrapped():
    assert _coerce_records({"records": [{"a": 1}]}, None) == [{"a": 1}]


def test_a_json_string_holding_an_envelope_is_unwrapped():
    assert _coerce_records('{"records": [{"a": 1}]}', None) == [{"a": 1}]


def test_records_fall_back_to_the_upstream_node_s_data():
    """Canvas wires one node's ``data`` into the next node's input."""
    assert _coerce_records(None, {"records": [{"a": 1}]}) == [{"a": 1}]
    assert _coerce_records(None, [{"a": 1}]) == [{"a": 1}]


def test_an_explicit_argument_beats_the_upstream_data():
    assert _coerce_records([{"explicit": 1}], {"records": [{"upstream": 1}]}) == [{"explicit": 1}]


def test_malformed_json_falls_through_to_the_upstream_data():
    assert _coerce_records("[not json", {"records": [{"a": 1}]}) == [{"a": 1}]


@pytest.mark.parametrize("records, data", [(None, None), ("plain text", None), (42, {}), ({}, {})])
def test_nothing_usable_yields_an_empty_batch_rather_than_raising(records, data):
    assert _coerce_records(records, data) == []


# ------------------------------------------------------------------ alignment
def test_ragged_records_are_given_the_same_columns():
    """pyarrow rejects a batch whose rows do not share a schema."""
    aligned = _align_records([{"a": 1}, {"b": 2}])
    assert aligned == [{"a": 1, "b": None}, {"a": None, "b": 2}]


def test_column_order_follows_first_appearance():
    aligned = _align_records([{"b": 1}, {"a": 2}])
    assert list(aligned[0]) == ["b", "a"]


def test_already_uniform_records_are_unchanged():
    records = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert _align_records(records) == records


def test_non_records_are_dropped_rather_than_poisoning_the_schema():
    assert _align_records([{"a": 1}, "not a record", None, 42]) == [{"a": 1}]


def test_an_empty_batch_aligns_to_an_empty_batch():
    assert _align_records([]) == []


def test_an_explicit_none_is_kept_as_a_value():
    """A null column value is data; it must not be confused with a missing key."""
    assert _align_records([{"a": None}, {"a": 1}]) == [{"a": None}, {"a": 1}]


# --------------------------------------------------------------------- token
def test_the_node_token_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "from-env")
    assert _resolve_token("from-node") == "from-node"


def test_the_environment_is_used_when_the_node_gives_nothing(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "from-env")
    assert _resolve_token("") == "from-env"
    assert _resolve_token("   ") == "from-env"


def test_no_token_anywhere_means_anonymous(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr("agentevolver.data.server.config", {}, raising=False)
    assert _resolve_token("") is None


# ---------------------------------------------------------------------- paths
@pytest.mark.parametrize(
    "repo",
    ["org/dataset", "../escape", "/etc/passwd", "  ", "a b", "..", ".", "....", "../.."],
)
def test_a_repo_name_becomes_one_directory_under_the_datasets_root(data, repo):
    """The name arrives from a model and is turned into a path.

    ``..`` and ``.`` matter specially: the character class allows ``.`` so
    version-ish names survive, which once let those two through intact.
    """
    from pathlib import Path

    resolved = Path(data._local_path(repo)).resolve()
    root = Path(data.base_dir).resolve()
    assert resolved.parent == root
    assert resolved != root


@pytest.mark.parametrize("repo", ["", "..", ".", "  ", "///"])
def test_a_repo_name_with_nothing_usable_falls_back_to_a_default(data, repo):
    assert data._local_path(repo).endswith("dataset")


def test_a_dot_inside_a_repo_name_is_kept(data):
    """Only the ends are stripped — ``ds.v1.0`` is a reasonable name."""
    assert data._local_path("ds.v1.0").endswith("ds.v1.0")


def test_a_slash_in_a_repo_name_does_not_create_a_nested_directory(data):
    assert data._local_path("org/dataset").endswith("org_dataset")


# ----------------------------------------------------------------- operations
@pytest.mark.asyncio
async def test_the_callable_operations_are_listed_for_step_validation(data):
    assert set(await data.list()) == set(DATA_OPERATIONS)


@pytest.mark.asyncio
async def test_an_unknown_operation_is_refused_by_name(data):
    result = await data("dataset_delete", {})
    assert result.success is False
    assert "dataset_delete" in result.message


@pytest.mark.asyncio
async def test_operation_info_is_available_for_known_names_only(data):
    assert (await data.get_info("dataset_save")).description
    assert await data.get_info("nonsense") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["dataset_save", "dataset_load"])
async def test_an_operation_without_a_repo_says_which_argument_is_missing(data, operation):
    result = await data(operation, {})
    assert result.success is False
    assert "'repo' is required" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["repo", "name", "dataset"])
async def test_the_dataset_may_be_named_by_any_of_its_aliases(data, alias, monkeypatch):
    """Upstream nodes disagree on the key; all three have to work."""
    seen = {}

    async def fake_save(payload):
        seen.update(payload)
        from agentevolver.response.types import Response, ResponseType

        return Response(type=ResponseType.TOOL, success=True, message="ok")

    monkeypatch.setattr(data, "_save", fake_save)
    await data("dataset_save", {alias: "org/ds"})
    assert seen[alias] == "org/ds"


# ---------------------------------------------------------------- local round trip
@pytest.mark.asyncio
async def test_records_survive_a_local_save_and_load(data):
    pytest.importorskip("datasets")

    saved = await data("dataset_save", {
        "repo": "org/ds", "target": "local",
        "records": [{"a": 1, "b": "x"}, {"a": 2}],
    })
    assert saved.success is True, saved.message
    assert saved.data["count"] == 2

    loaded = await data("dataset_load", {"repo": "org/ds", "source": "local"})
    assert loaded.success is True, loaded.message
    assert loaded.data["records"] == [{"a": 1, "b": "x"}, {"a": 2, "b": None}]


@pytest.mark.asyncio
async def test_loading_a_dataset_that_was_never_saved_is_a_failed_result(data):
    """A missing dataset must not raise into the canvas run."""
    pytest.importorskip("datasets")

    result = await data("dataset_load", {"repo": "never-saved", "source": "local"})
    assert result.success is False
    assert "dataset_load:" in result.message


@pytest.mark.asyncio
async def test_a_saved_dataset_reports_its_path_as_a_produced_file(data):
    pytest.importorskip("datasets")

    result = await data("dataset_save", {"repo": "org/ds", "target": "local", "records": [{"a": 1}]})
    assert result.files == [result.data["path"]]
