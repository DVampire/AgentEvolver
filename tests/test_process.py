"""Process: pure record transforms, and the canvas plumbing in front of them.

Processors are declared side-effect free — same input, same output — so the interesting
behaviour is at the edges. Canvas compiles list/object ports to JSON *strings*, so a
``["a","b"]`` literal reaches a processor as `'["a","b"]'`, and wiring a capability's
``data`` port hands over the whole ``{records, count}`` envelope rather than the rows.
Every processor has to see through both, or a correctly-drawn graph fails at runtime.

The second theme is that arguments arrive from a model or from a hand-drawn node, which
means `n` can be the string "2", a filter value can be "1" where a number was meant, and a
sort key can be missing from half the rows. A transform that raises on any of those takes
down a pipeline stage over an argument nobody could have typed correctly, so each one
degrades to something a reader can recognise instead.
"""

import pytest

from agentevolver.process.default.records import (
    DeriveReturnProcessor,
    FilterRowsProcessor,
    HeadProcessor,
    RenameFieldsProcessor,
    SelectFieldsProcessor,
    SortRecordsProcessor,
    ToEvalRecordsProcessor,
    _as_list,
    _coerce_records,
)
from agentevolver.process.server import ProcessManager
from agentevolver.process.types import ProcessContext, Processor
from agentevolver.response.types import Response, ResponseType

ROWS = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}]


# --------------------------------------------------------------------------- #
# Arguments as the canvas compiles them
# --------------------------------------------------------------------------- #
def test_a_json_string_argument_is_parsed_back_into_a_list():
    """List ports compile to JSON strings in the workflow language.

    Without this the field list a user typed into a node arrives as one long string, and
    `select_fields` keeps a single field literally named ``["a", "b"]`` — a graph that is
    drawn correctly and returns nothing.
    """
    assert _as_list('["a", "b"]') == ["a", "b"]
    assert _as_list('{"old": "new"}') == {"old": "new"}


def test_a_genuine_list_is_left_alone():
    """The same argument arrives already parsed when it comes from code rather than canvas."""
    assert _as_list(["a"]) == ["a"]


def test_a_non_json_string_is_not_mangled():
    """Only bracket-leading text is even tried, and a failed parse returns the original.

    ``"[not json"`` is the case that matters: it looks like JSON, so it reaches the parser,
    and raising there would fail a node over a string that was always meant to be a plain
    value.
    """
    assert _as_list("plain") == "plain"
    assert _as_list("[not json") == "[not json"


def test_a_data_envelope_is_unwrapped_to_its_rows():
    """Connecting a capability's ``data`` port yields the whole envelope.

    The natural wiring on a canvas is source.data → process.records, and what travels down
    that wire is ``{records, count}``, not the rows. A processor that iterated it directly
    would walk the two keys of a dict.
    """
    assert _coerce_records({"records": ROWS, "count": 3}, None) == ROWS


def test_rows_fall_back_to_the_upstream_data_argument():
    """A node may be wired through ``data`` instead of ``records``, in either shape."""
    assert _coerce_records(None, {"records": ROWS}) == ROWS
    assert _coerce_records(None, ROWS) == ROWS


def test_a_json_string_of_rows_is_parsed():
    assert _coerce_records('[{"a": 1}]', None) == [{"a": 1}]


@pytest.mark.parametrize("records, data", [(None, None), ("text", None), (42, {})])
def test_nothing_usable_yields_no_rows_rather_than_raising(records, data):
    """An unconnected port is a graph still being drawn, not a crash.

    Returning an empty batch lets the rest of the pipeline run and report zero records,
    which is a state the author can see on the canvas; an exception loses the run.
    """
    assert _coerce_records(records, data) == []


# --------------------------------------------------------------------------- #
# Keeping only some fields
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_select_keeps_only_the_named_fields():
    result = await SelectFieldsProcessor()(records=ROWS, fields=["a"])
    assert result.data["records"] == [{"a": 1}, {"a": 2}, {"a": 3}]
    assert result.data["count"] == 3


@pytest.mark.asyncio
async def test_select_accepts_its_field_list_as_a_compiled_string():
    result = await SelectFieldsProcessor()(records=ROWS, fields='["a"]')
    assert result.data["records"] == [{"a": 1}, {"a": 2}, {"a": 3}]


@pytest.mark.asyncio
async def test_selecting_a_field_no_row_has_yields_nulls_not_a_crash():
    """A misspelled field name is the most likely thing to be wrong about a dataset.

    Nulls make the mistake visible in the output — every row present, every value empty —
    where a KeyError would only say that something went wrong somewhere in the batch.
    """
    result = await SelectFieldsProcessor()(records=ROWS, fields=["missing"])
    assert result.data["records"] == [{"missing": None}] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [None, [], "not-a-list"])
async def test_select_without_fields_is_refused(fields):
    """Selecting nothing has no sensible reading: not "keep everything", not "keep none".

    The three shapes are the three ways the argument goes missing — unwired, emptied, or
    typed as loose text — and each is refused by name so the message points at the port
    that needs attention.
    """
    result = await SelectFieldsProcessor()(records=ROWS, fields=fields)
    assert result.success is False
    assert "'fields' is required" in result.message


# --------------------------------------------------------------------------- #
# Taking the first few
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_head_takes_the_first_n():
    assert (await HeadProcessor()(records=ROWS, n=2)).data["records"] == ROWS[:2]


@pytest.mark.asyncio
async def test_head_asking_for_more_than_exists_returns_everything():
    """Slicing past the end is normal on a small batch, not an error to report."""
    assert (await HeadProcessor()(records=ROWS, n=99)).data["count"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("n, expected", [("2", 2), (-1, 0), ("nonsense", 3), (None, 3)])
async def test_head_survives_whatever_n_the_model_supplies(n, expected):
    """`n` comes from a model or a canvas field, so every one of these really arrives.

    "2" is the common one: a numeric port compiled to a string. -1 clamps to an empty
    batch rather than slicing from the end, which is what a bare `rows[:-1]` would quietly
    do — dropping the last row instead of taking none. "nonsense" and None fall back to
    the default of 10, which is why they return all 3 rows here.
    """
    assert (await HeadProcessor()(records=ROWS, n=n)).data["count"] == expected


# --------------------------------------------------------------------------- #
# Renaming fields
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rename_maps_the_named_keys_and_leaves_the_rest():
    """The mapping names what changes; anything unmentioned has to survive untouched.

    A rename implemented as "build a new row from the mapping" drops every other field,
    and the loss shows up downstream rather than here.
    """
    result = await RenameFieldsProcessor()(records=ROWS, mapping={"a": "alpha"})
    assert result.data["records"][0] == {"alpha": 1, "b": "x"}


@pytest.mark.asyncio
async def test_rename_accepts_its_mapping_as_a_compiled_string():
    result = await RenameFieldsProcessor()(records=ROWS, mapping='{"a": "alpha"}')
    assert "alpha" in result.data["records"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mapping", [None, {}, ["a"]])
async def test_rename_without_a_mapping_is_refused(mapping):
    """A list is the interesting shape: it parses fine and means nothing as a mapping."""
    assert (await RenameFieldsProcessor()(records=ROWS, mapping=mapping)).success is False


# --------------------------------------------------------------------------- #
# Filtering rows
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op, value, kept",
    [
        ("eq", 2, [2]),
        ("ne", 2, [1, 3]),
        ("gt", 1, [2, 3]),
        ("ge", 2, [2, 3]),
        ("lt", 3, [1, 2]),
        ("le", 2, [1, 2]),
    ],
)
async def test_each_comparison_selects_the_right_rows(op, value, kept):
    """Each operator is spelled out because the boundary cases differ by one row.

    `gt`/`ge` and `lt`/`le` are one character apart in the graph and one row apart in the
    result, and a filter that silently includes or excludes its boundary changes a
    downstream count without changing anything visible.
    """
    result = await FilterRowsProcessor()(records=ROWS, field="a", op=op, value=value)
    assert [row["a"] for row in result.data["records"]] == kept


@pytest.mark.asyncio
async def test_a_numeric_string_is_compared_numerically():
    """The value arrives from a canvas arg; ``"2" > 1`` would raise on strings."""
    result = await FilterRowsProcessor()(records=ROWS, field="a", op="gt", value="1")
    assert [row["a"] for row in result.data["records"]] == [2, 3]


@pytest.mark.asyncio
async def test_a_missing_value_never_satisfies_an_ordering_comparison():
    """Comparing None with a number would raise; those rows are simply excluded.

    Real datasets have gaps, so one row without the field would otherwise fail the whole
    batch. Excluding is also the only defensible answer: a missing value is not greater
    than anything, and treating it as zero would invent data.
    """
    rows = [{"a": 1}, {"b": 2}]
    result = await FilterRowsProcessor()(records=rows, field="a", op="gt", value=0)
    assert result.data["records"] == [{"a": 1}]


@pytest.mark.asyncio
async def test_filter_without_a_field_is_refused():
    """No field means no predicate; passing everything through would look like a filter."""
    assert (await FilterRowsProcessor()(records=ROWS, field="")).success is False


@pytest.mark.asyncio
async def test_an_unknown_comparison_is_refused_by_name():
    """A model reaching for `matches` needs to be told that operator does not exist.

    Falling back to the default `eq` would answer a question nobody asked and return rows
    that look like a successful filter.
    """
    result = await FilterRowsProcessor()(records=ROWS, field="a", op="matches")
    assert result.success is False
    assert "matches" in result.message


# --------------------------------------------------------------------------- #
# Deriving a column from the row before
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_first_row_has_no_previous_value_to_compare_against():
    """None, not zero: there is no return on the first observation.

    Zero would read as "unchanged" and become a real data point in whatever averages the
    column afterwards, which is a bias no later stage can detect.
    """
    rows = [{"close": 100}, {"close": 110}, {"close": 99}]
    records = (await DeriveReturnProcessor()(records=rows)).data["records"]
    assert records[0]["return"] is None
    assert records[1]["return"] == pytest.approx(0.1)
    assert records[2]["return"] == pytest.approx(-0.1)


@pytest.mark.asyncio
async def test_a_zero_price_does_not_divide_by_zero():
    """A zero baseline is a bad row in the data, not a reason to fail the batch."""
    records = (await DeriveReturnProcessor()(records=[{"close": 0}, {"close": 5}])).data["records"]
    assert records[1]["return"] is None


@pytest.mark.asyncio
async def test_a_gap_in_the_series_does_not_advance_the_baseline():
    """A non-numeric row must not become the previous value.

    The third row's return is measured against 100, the last real price, not against the
    gap — so a missing observation costs one row of output rather than corrupting every
    row after it.
    """
    rows = [{"close": 100}, {"close": None}, {"close": 110}]
    records = (await DeriveReturnProcessor()(records=rows)).data["records"]
    assert records[1]["return"] is None
    assert records[2]["return"] == pytest.approx(0.1)


# --------------------------------------------------------------------------- #
# Shaping rows for a benchmark
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rows_are_reshaped_for_a_benchmark_node():
    """This is the join between a data pipeline and evaluation.

    A benchmark node reads exactly `{task_id, prediction, ground_truth}`, so whatever the
    upstream source called its columns has to be renamed here or the evaluation scores
    empty predictions.
    """
    rows = [{"pred": "a", "truth": "b"}]
    result = await ToEvalRecordsProcessor()(
        records=rows,
        prediction_field="pred",
        ground_truth_field="truth",
    )
    assert result.data["records"] == [{"task_id": "0", "prediction": "a", "ground_truth": "b"}]


@pytest.mark.asyncio
async def test_the_row_index_stands_in_for_a_missing_id():
    """Ids have to be distinct: a shared id makes two results collide into one score."""
    rows = [{"prediction": "a"}, {"prediction": "b"}]
    records = (await ToEvalRecordsProcessor()(records=rows)).data["records"]
    assert [r["task_id"] for r in records] == ["0", "1"]


@pytest.mark.asyncio
async def test_an_id_field_is_used_when_named():
    """Stringified, because ids from a dataset are as often integers as not."""
    rows = [{"qid": 42, "prediction": "a"}]
    records = (await ToEvalRecordsProcessor()(records=rows, id_field="qid")).data["records"]
    assert records[0]["task_id"] == "42"


# --------------------------------------------------------------------------- #
# Sorting
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sorting_orders_by_the_key():
    rows = [{"a": 3}, {"a": 1}, {"a": 2}]
    result = await SortRecordsProcessor()(records=rows, key="a")
    assert [r["a"] for r in result.data["records"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_sorting_can_be_reversed():
    rows = [{"a": 1}, {"a": 3}, {"a": 2}]
    result = await SortRecordsProcessor()(records=rows, key="a", descending=True)
    assert [r["a"] for r in result.data["records"]] == [3, 2, 1]


@pytest.mark.asyncio
async def test_rows_missing_the_key_sort_last_instead_of_raising():
    """Python refuses to order None against an int, so one gap would fail the whole sort.

    Sorting the missing rows to the end keeps them in the output — dropping them would be
    the other tempting fix, and it loses records the user never asked to filter.
    """
    rows = [{"a": 2}, {"b": 1}, {"a": 1}]
    result = await SortRecordsProcessor()(records=rows, key="a")
    assert [r.get("a") for r in result.data["records"]] == [1, 2, None]


@pytest.mark.asyncio
async def test_sort_without_a_key_is_refused():
    assert (await SortRecordsProcessor()(records=ROWS, key="")).success is False


# --------------------------------------------------------------------------- #
# The properties every transform shares
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "processor, kwargs",
    [
        (SelectFieldsProcessor(), {"fields": ["a"]}),
        (HeadProcessor(), {"n": 1}),
        (RenameFieldsProcessor(), {"mapping": {"a": "alpha"}}),
        (FilterRowsProcessor(), {"field": "a", "op": "gt", "value": 1}),
        (SortRecordsProcessor(), {"key": "a", "descending": True}),
        (DeriveReturnProcessor(), {"field": "a"}),
        (ToEvalRecordsProcessor(), {}),
    ],
)
async def test_a_transform_never_mutates_its_input(processor, kwargs):
    """Purity is the contract, and one branch of a graph can feed several nodes.

    A processor that edited rows in place would change what a *sibling* node sees, so the
    same graph produces different results depending on which branch ran first. That is
    invisible in a single-branch pipeline and unreproducible in a real one — which is why
    this is asserted for every processor rather than for the ones that look risky.
    """
    rows = [dict(row) for row in ROWS]
    await processor(records=rows, **kwargs)
    assert rows == ROWS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "processor, kwargs",
    [
        (SelectFieldsProcessor(), {"fields": ["a"]}),
        (HeadProcessor(), {}),
        (RenameFieldsProcessor(), {"mapping": {"a": "b"}}),
        (FilterRowsProcessor(), {"field": "a"}),
        (SortRecordsProcessor(), {"key": "a"}),
        (DeriveReturnProcessor(), {}),
        (ToEvalRecordsProcessor(), {}),
    ],
)
async def test_an_empty_batch_flows_through_every_transform(processor, kwargs):
    """An upstream filter matching nothing must not break every stage after it.

    Success with zero records is the honest report; a failure here would be blamed on the
    processor rather than on the filter that emptied the batch, and a whole pipeline goes
    red for a dataset that simply had no matching rows.
    """
    result = await processor(records=[], **kwargs)
    assert result.success is True
    assert result.data["records"] == []


# --------------------------------------------------------------------------- #
# Registering and invoking by name
# --------------------------------------------------------------------------- #
class Doubler(Processor):
    name: str = "doubler"
    description: str = "Doubles a number"

    async def __call__(self, value: int = 0, **kwargs):
        return Response(
            type=ResponseType.TOOL, success=True, message="ok", data={"value": value * 2}
        )


class Exploding(Processor):
    name: str = "boom"

    async def __call__(self, **kwargs):
        raise RuntimeError("bad transform")


@pytest.fixture
def process():
    """A manager with nothing in it — ``initialize`` would load the real registry."""
    return ProcessManager()


@pytest.mark.asyncio
async def test_a_registered_processor_can_be_invoked_by_name(process):
    """The name in a workflow's `<process>` step is the only handle the runtime has."""
    await process.register(Doubler())
    assert (await process("doubler", {"value": 21})).data["value"] == 42


@pytest.mark.asyncio
async def test_registering_the_same_name_twice_is_refused_by_default(process):
    """Two processors under one name means half the graph silently calls the other one.

    Overriding stays possible because reloading an evolved processor needs it — but as a
    stated argument, so it cannot happen by accident when two modules pick the same name.
    """
    await process.register(Doubler())
    with pytest.raises(ValueError, match="already registered"):
        await process.register(Doubler())
    await process.register(Doubler(), override=True)  # explicit override is allowed


@pytest.mark.asyncio
async def test_an_unknown_processor_is_refused_by_name(process):
    """The name is echoed back because the usual cause is a typo in the graph."""
    result = await process("nonesuch", {})
    assert result.success is False
    assert "nonesuch" in result.message


@pytest.mark.asyncio
async def test_a_bad_transform_is_a_failed_result_not_a_crash(process):
    """A processor blowing up must not take the workflow run with it.

    The step fails, the run records why, and the retry and checkpoint machinery upstream
    gets to do its job — none of which happens if the exception escapes the manager.
    """
    await process.register(Exploding())
    result = await process("boom", {})
    assert result.success is False
    assert "bad transform" in result.message


@pytest.mark.asyncio
async def test_the_registry_is_listable_and_clearable(process):
    """Listing feeds the capability roster; an unknown name answers None rather than raising."""
    await process.register(Doubler())
    assert await process.list() == ["doubler"]
    assert (await process.get("doubler")).name == "doubler"
    assert await process.get("ghost") is None
    await process.cleanup()
    assert await process.list() == []


@pytest.mark.asyncio
async def test_a_processor_without_a_call_body_is_refused():
    """The base class raising is what stops a half-written processor reporting success."""
    with pytest.raises(NotImplementedError):
        await Processor(name="bare")()


def test_a_process_context_needs_nothing_to_be_constructed():
    """Callers build a context before they know the payload, so every field has a default."""
    assert ProcessContext().input == {}
