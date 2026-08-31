"""Whatever an upstream node hands the data node has to become a pyarrow-shaped batch.

``dataset_save`` and ``dataset_load`` are canvas nodes: their ``records`` argument is
whatever the node before them produced, or whatever a model wrote into the step's
arguments. That means a list, a JSON string, or a ``{"records": [...]}`` envelope — and
the rows inside are ragged, because nothing upstream promises a schema.

Both problems fail late and far away. HuggingFace builds a ``Dataset`` through pyarrow,
which rejects a batch whose rows do not share a column set; the traceback names arrow,
not the node that emitted the rows. And a ``repo`` name arrives from a model and is used
to build a filesystem path, so ``..`` and ``.`` have to stop being path components before
they reach ``os.path.join``.

The Hub round trip itself is network-bound and not exercised here; the local save/load
pair stands in for it, since both directions go through the same ``Dataset``.
"""

from pathlib import Path

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


# --------------------------------------------------------------------------- #
# Finding the records in whatever the node was given
# --------------------------------------------------------------------------- #
def test_a_plain_list_of_records_is_taken_as_is():
    assert _coerce_records([{"a": 1}], None) == [{"a": 1}]


def test_a_json_string_is_parsed_rather_than_treated_as_one_value():
    """A model asked for a list argument often writes the list as a string instead.

    Both spellings are ordinary model output and neither is an error, so refusing the
    string would turn a routine formatting choice into a lost batch — and the node would
    report success with zero rows saved.
    """
    assert _coerce_records('[{"a": 1}]', None) == [{"a": 1}]


def test_a_records_envelope_is_unwrapped():
    """Node outputs are ``{message, data, files}``; ``data`` usually wraps its rows."""
    assert _coerce_records({"records": [{"a": 1}]}, None) == [{"a": 1}]


def test_a_json_string_holding_an_envelope_is_unwrapped():
    """The two awkward cases compose: serialised *and* wrapped has to work in one pass."""
    assert _coerce_records('{"records": [{"a": 1}]}', None) == [{"a": 1}]


def test_records_fall_back_to_the_upstream_node_s_data():
    """Canvas wires one node's ``data`` into the next node's input.

    A wired edge leaves ``records`` unset entirely, so treating "no argument" as "no rows"
    would make every wired-up save write an empty dataset — the common way the node is
    used, failing silently.
    """
    assert _coerce_records(None, {"records": [{"a": 1}]}) == [{"a": 1}]
    assert _coerce_records(None, [{"a": 1}]) == [{"a": 1}]


def test_an_explicit_argument_beats_the_upstream_data():
    """When the step names its rows, the wired edge is context, not the answer."""
    assert _coerce_records([{"explicit": 1}], {"records": [{"upstream": 1}]}) == [{"explicit": 1}]


def test_malformed_json_falls_through_to_the_upstream_data():
    """Unparseable text is treated as "no argument given", not as a fatal error.

    The upstream rows are still right there, and raising would take down a canvas run over
    a truncated string the node did not write.
    """
    assert _coerce_records("[not json", {"records": [{"a": 1}]}) == [{"a": 1}]


@pytest.mark.parametrize("records, data", [(None, None), ("plain text", None), (42, {}), ({}, {})])
def test_nothing_usable_yields_an_empty_batch_rather_than_raising(records, data):
    """Every "this is not records" shape resolves to the same empty list.

    The alternative is a ``TypeError`` raised from inside a canvas step, which surfaces as
    a crashed run rather than as a node that saved nothing.
    """
    assert _coerce_records(records, data) == []


# --------------------------------------------------------------------------- #
# Giving ragged rows one schema
# --------------------------------------------------------------------------- #
def test_ragged_records_are_given_the_same_columns():
    """pyarrow rejects a batch whose rows do not share a column set.

    Rows built by an agent step by step naturally diverge — one run recorded an error
    field, the next did not. Aligning to the union with ``None`` for the gaps is what
    keeps that from becoming an arrow schema error thrown far from the node that produced
    the rows.
    """
    aligned = _align_records([{"a": 1}, {"b": 2}])
    assert aligned == [{"a": 1, "b": None}, {"a": None, "b": 2}]


def test_columns_keep_the_order_they_first_appeared_in():
    """Column order is what a human sees when they open the dataset.

    Sorting the union alphabetically would be just as valid to arrow and would scramble a
    deliberate field order — id first, then inputs, then results — on every save.
    """
    aligned = _align_records([{"b": 1}, {"a": 2}])
    assert list(aligned[0]) == ["b", "a"]


def test_already_uniform_records_are_unchanged():
    """The common case must pass through without reordering or rewriting anything."""
    records = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert _align_records(records) == records


def test_non_records_are_dropped_rather_than_poisoning_the_schema():
    """One stray scalar in the list would otherwise decide the whole batch's fate.

    A string or ``None`` mixed in among the rows has no keys to contribute and no key to
    be indexed by; keeping it means arrow sees a batch it cannot type at all, so every
    good row is lost along with the bad one.
    """
    assert _align_records([{"a": 1}, "not a record", None, 42]) == [{"a": 1}]


def test_an_empty_batch_aligns_to_an_empty_batch():
    assert _align_records([]) == []


def test_an_explicit_none_is_kept_as_a_value():
    """A null column value is data; it must not be confused with a missing key.

    The alignment fills gaps with ``None``, so the two become indistinguishable if the
    implementation drops falsy values on the way in — and a column that was deliberately
    null starts reading as a column that was never set.
    """
    assert _align_records([{"a": None}, {"a": 1}]) == [{"a": None}, {"a": 1}]


# --------------------------------------------------------------------------- #
# Which token the Hub call uses
# --------------------------------------------------------------------------- #
def test_the_node_token_wins_over_the_environment(monkeypatch):
    """A canvas that names its token means to push as that identity, not as the host's."""
    monkeypatch.setenv("HF_TOKEN", "from-env")
    assert _resolve_token("from-node") == "from-node"


def test_a_blank_node_token_falls_back_to_the_environment(monkeypatch):
    """An unset node field arrives as ``""`` or whitespace, never as ``None``.

    A truthiness check alone lets `"   "` through as a token, and the Hub then rejects the
    push with an auth error while a perfectly good ``HF_TOKEN`` sat unused.
    """
    monkeypatch.setenv("HF_TOKEN", "from-env")
    assert _resolve_token("") == "from-env"
    assert _resolve_token("   ") == "from-env"


def test_no_token_anywhere_means_anonymous(monkeypatch):
    """``None`` is the Hub's anonymous mode, which is the right answer for public data.

    Returning ``""`` instead would be sent as a credential and rejected, turning a working
    read of a public dataset into an auth failure.
    """
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr("agentevolver.data.server.config", {}, raising=False)
    assert _resolve_token("") is None


# --------------------------------------------------------------------------- #
# Turning a model-supplied name into a path
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "repo",
    ["org/dataset", "../escape", "/etc/passwd", "  ", "a b", "..", ".", "....", "../.."],
)
def test_a_repo_name_becomes_one_directory_under_the_datasets_root(data, repo):
    """The name arrives from a model and is turned into a path.

    ``..`` and ``.`` matter specially: the character class allows ``.`` so version-ish
    names survive, which once let those two through intact — ``..`` resolved a level above
    the datasets root and ``.`` resolved to the root itself, so a save would have written
    over the root directory rather than into it. Both halves of the assertion are needed:
    directly under the root, and not the root.
    """
    resolved = Path(data._local_path(repo)).resolve()
    root = Path(data.base_dir).resolve()
    assert resolved.parent == root
    assert resolved != root


@pytest.mark.parametrize("repo", ["", "..", ".", "  ", "///"])
def test_a_repo_name_with_nothing_usable_falls_back_to_a_default(data, repo):
    """Sanitising these to the empty string would make the path the root directory itself."""
    assert data._local_path(repo).endswith("dataset")


def test_a_dot_inside_a_repo_name_is_kept(data):
    """Only the ends are stripped — ``ds.v1.0`` is a reasonable name.

    Banning ``.`` outright is the easy fix for ``..`` and it mangles every versioned
    dataset name into one with underscores, which then no longer matches the Hub repo it
    came from.
    """
    assert data._local_path("ds.v1.0").endswith("ds.v1.0")


def test_a_slash_in_a_repo_name_does_not_create_a_nested_directory(data):
    """``org/dataset`` is one Hub repo id, not a two-level path.

    Joining it as a path would scatter local copies into per-org directories that nothing
    else knows to look in, and would put the escape sequences above back in play.
    """
    assert data._local_path("org/dataset").endswith("org_dataset")


# --------------------------------------------------------------------------- #
# The operations a canvas step may name
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_callable_operations_are_listed_for_step_validation(data):
    """Canvas validates a step's name against this list before the run starts.

    A name missing here is rejected at build time even though calling it would work, so
    the list and the dispatch have to agree exactly — hence the set comparison rather than
    a membership check.
    """
    assert set(await data.list()) == set(DATA_OPERATIONS)


@pytest.mark.asyncio
async def test_an_unknown_operation_is_refused_by_name(data):
    """A failed result, not an exception — and it has to quote the name it refused.

    ``dataset_delete`` is the sort of name a model invents by analogy. Without the name in
    the message the author sees a generic failure and no clue that the operation simply
    does not exist.
    """
    result = await data("dataset_delete", {})
    assert result.success is False
    assert "dataset_delete" in result.message


@pytest.mark.asyncio
async def test_operation_info_is_available_for_known_names_only(data):
    """``None`` for an unknown name is what lets a catalog distinguish it from a described one."""
    assert (await data.get_info("dataset_save")).description
    assert await data.get_info("nonsense") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["dataset_save", "dataset_load"])
async def test_an_operation_without_a_repo_says_which_argument_is_missing(data, operation):
    """Both directions need a repo, and both have to name it in the failure.

    The caller here is often a model reading the message back; "invalid arguments" gives
    it nothing to correct, while the argument's name gives it the next step.
    """
    result = await data(operation, {})
    assert result.success is False
    assert "'repo' is required" in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["repo", "name", "dataset"])
async def test_the_dataset_may_be_named_by_any_of_its_aliases(data, alias, monkeypatch):
    """Upstream nodes disagree on the key; all three have to work.

    Collapsing to one canonical spelling looks like the tidy fix and breaks every canvas
    already wired with another. The fake save records the payload it received, so the test
    checks the alias reached the operation rather than that a save succeeded.
    """
    seen = {}

    async def fake_save(payload):
        seen.update(payload)
        from agentevolver.response.types import Response, ResponseType

        return Response(type=ResponseType.TOOL, success=True, message="ok")

    monkeypatch.setattr(data, "_save", fake_save)
    await data("dataset_save", {alias: "org/ds"})
    assert seen[alias] == "org/ds"


# --------------------------------------------------------------------------- #
# A save and a load agree with each other
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_records_survive_a_local_save_and_load(data):
    """The alignment is only worth anything if it is what actually reaches disk.

    Every unit above tests ``_align_records`` in isolation; this is the one place the
    ragged rows go through ``Dataset`` and come back. The second row is deliberately short
    a column, so the loaded batch shows the filled ``None`` rather than the original gap.
    """
    pytest.importorskip("datasets")

    saved = await data(
        "dataset_save",
        {
            "repo": "org/ds",
            "target": "local",
            "records": [{"a": 1, "b": "x"}, {"a": 2}],
        },
    )
    assert saved.success is True, saved.message
    assert saved.data["count"] == 2

    loaded = await data("dataset_load", {"repo": "org/ds", "source": "local"})
    assert loaded.success is True, loaded.message
    assert loaded.data["records"] == [{"a": 1, "b": "x"}, {"a": 2, "b": None}]


@pytest.mark.asyncio
async def test_loading_a_dataset_that_was_never_saved_is_a_failed_result(data):
    """A missing dataset must not raise into the canvas run.

    ``load_from_disk`` throws on a path that is not there, and a canvas step that throws
    takes the whole run with it. The prefix in the message is what tells the reader which
    node the failure came from once it is one line among many.
    """
    pytest.importorskip("datasets")

    result = await data("dataset_load", {"repo": "never-saved", "source": "local"})
    assert result.success is False
    assert "dataset_load:" in result.message


@pytest.mark.asyncio
async def test_a_saved_dataset_reports_its_path_as_a_produced_file(data):
    """``files`` is what the UI links and what a later node consumes.

    A path present in ``data`` but absent from ``files`` is invisible to both, so the save
    appears to have produced nothing.
    """
    pytest.importorskip("datasets")

    result = await data(
        "dataset_save", {"repo": "org/ds", "target": "local", "records": [{"a": 1}]}
    )
    assert result.files == [result.data["path"]]
