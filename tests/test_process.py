"""Process: pure record transforms, and the canvas plumbing in front of them.

Processors are declared side-effect free — same input, same output — so the
interesting behaviour is at the edges. Canvas compiles list/object ports to JSON
*strings*, so a ``["a","b"]`` literal reaches a processor as `'["a","b"]'`, and
wiring a capability's ``data`` port hands over the whole ``{records, count}``
envelope rather than the rows. Every processor has to see through both, or a
correctly-drawn graph fails at runtime.
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


# -------------------------------------------------------------- canvas args
def test_a_json_string_argument_is_parsed_back_into_a_list():
    """List ports compile to JSON strings in the workflow language."""
    assert _as_list('["a", "b"]') == ["a", "b"]
    assert _as_list('{"old": "new"}') == {"old": "new"}


def test_a_genuine_list_is_left_alone():
    assert _as_list(["a"]) == ["a"]


def test_a_non_json_string_is_not_mangled():
    assert _as_list("plain") == "plain"
    assert _as_list("[not json") == "[not json"


def test_a_data_envelope_is_unwrapped_to_its_rows():
    """Connecting a capability's ``data`` port yields the whole envelope."""
    assert _coerce_records({"records": ROWS, "count": 3}, None) == ROWS


def test_rows_fall_back_to_the_upstream_data_argument():
    assert _coerce_records(None, {"records": ROWS}) == ROWS
    assert _coerce_records(None, ROWS) == ROWS


def test_a_json_string_of_rows_is_parsed():
    assert _coerce_records('[{"a": 1}]', None) == [{"a": 1}]


@pytest.mark.parametrize("records, data", [(None, None), ("text", None), (42, {})])
def test_nothing_usable_yields_no_rows_rather_than_raising(records, data):
    assert _coerce_records(records, data) == []


# ---------------------------------------------------------------- select
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
    result = await SelectFieldsProcessor()(records=ROWS, fields=["missing"])
    assert result.data["records"] == [{"missing": None}] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [None, [], "not-a-list"])
async def test_select_without_fields_is_refused(fields):
    result = await SelectFieldsProcessor()(records=ROWS, fields=fields)
    assert result.success is False
    assert "'fields' is required" in result.message


# ------------------------------------------------------------------ head
@pytest.mark.asyncio
async def test_head_takes_the_first_n():
    assert (await HeadProcessor()(records=ROWS, n=2)).data["records"] == ROWS[:2]


@pytest.mark.asyncio
async def test_head_asking_for_more_than_exists_returns_everything():
    assert (await HeadProcessor()(records=ROWS, n=99)).data["count"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("n, expected", [("2", 2), (-1, 0), ("nonsense", 3), (None, 3)])
async def test_head_survives_whatever_n_the_model_supplies(n, expected):
    assert (await HeadProcessor()(records=ROWS, n=n)).data["count"] == expected


# ---------------------------------------------------------------- rename
@pytest.mark.asyncio
async def test_rename_maps_the_named_keys_and_leaves_the_rest():
    result = await RenameFieldsProcessor()(records=ROWS, mapping={"a": "alpha"})
    assert result.data["records"][0] == {"alpha": 1, "b": "x"}


@pytest.mark.asyncio
async def test_rename_accepts_its_mapping_as_a_compiled_string():
    result = await RenameFieldsProcessor()(records=ROWS, mapping='{"a": "alpha"}')
    assert "alpha" in result.data["records"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mapping", [None, {}, ["a"]])
async def test_rename_without_a_mapping_is_refused(mapping):
    assert (await RenameFieldsProcessor()(records=ROWS, mapping=mapping)).success is False


# ---------------------------------------------------------------- filter
@pytest.mark.asyncio
@pytest.mark.parametrize("op, value, kept", [
    ("eq", 2, [2]), ("ne", 2, [1, 3]),
    ("gt", 1, [2, 3]), ("ge", 2, [2, 3]),
    ("lt", 3, [1, 2]), ("le", 2, [1, 2]),
])
async def test_each_comparison_selects_the_right_rows(op, value, kept):
    result = await FilterRowsProcessor()(records=ROWS, field="a", op=op, value=value)
    assert [row["a"] for row in result.data["records"]] == kept


@pytest.mark.asyncio
async def test_a_numeric_string_is_compared_numerically():
    """The value arrives from a canvas arg; ``"2" > 1`` would raise on strings."""
    result = await FilterRowsProcessor()(records=ROWS, field="a", op="gt", value="1")
    assert [row["a"] for row in result.data["records"]] == [2, 3]


@pytest.mark.asyncio
async def test_a_missing_value_never_satisfies_an_ordering_comparison():
    """Comparing None with a number would raise; those rows are simply excluded."""
    rows = [{"a": 1}, {"b": 2}]
    result = await FilterRowsProcessor()(records=rows, field="a", op="gt", value=0)
    assert result.data["records"] == [{"a": 1}]


@pytest.mark.asyncio
async def test_filter_without_a_field_is_refused():
    assert (await FilterRowsProcessor()(records=ROWS, field="")).success is False


@pytest.mark.asyncio
async def test_an_unknown_comparison_is_refused_by_name():
    result = await FilterRowsProcessor()(records=ROWS, field="a", op="matches")
    assert result.success is False
    assert "matches" in result.message


# ----------------------------------------------------------------- derive
@pytest.mark.asyncio
async def test_the_first_row_has_no_previous_value_to_compare_against():
    rows = [{"close": 100}, {"close": 110}, {"close": 99}]
    records = (await DeriveReturnProcessor()(records=rows)).data["records"]
    assert records[0]["return"] is None
    assert records[1]["return"] == pytest.approx(0.1)
    assert records[2]["return"] == pytest.approx(-0.1)


@pytest.mark.asyncio
async def test_a_zero_price_does_not_divide_by_zero():
    records = (await DeriveReturnProcessor()(records=[{"close": 0}, {"close": 5}])).data["records"]
    assert records[1]["return"] is None


@pytest.mark.asyncio
async def test_a_gap_in_the_series_does_not_advance_the_baseline():
    """A non-numeric row must not become the previous value."""
    rows = [{"close": 100}, {"close": None}, {"close": 110}]
    records = (await DeriveReturnProcessor()(records=rows)).data["records"]
    assert records[1]["return"] is None
    assert records[2]["return"] == pytest.approx(0.1)


# ------------------------------------------------------------- eval records
@pytest.mark.asyncio
async def test_rows_are_reshaped_for_a_benchmark_node():
    rows = [{"pred": "a", "truth": "b"}]
    result = await ToEvalRecordsProcessor()(
        records=rows, prediction_field="pred", ground_truth_field="truth",
    )
    assert result.data["records"] == [{"task_id": "0", "prediction": "a", "ground_truth": "b"}]


@pytest.mark.asyncio
async def test_the_row_index_stands_in_for_a_missing_id():
    rows = [{"prediction": "a"}, {"prediction": "b"}]
    records = (await ToEvalRecordsProcessor()(records=rows)).data["records"]
    assert [r["task_id"] for r in records] == ["0", "1"]


@pytest.mark.asyncio
async def test_an_id_field_is_used_when_named():
    rows = [{"qid": 42, "prediction": "a"}]
    records = (await ToEvalRecordsProcessor()(records=rows, id_field="qid")).data["records"]
    assert records[0]["task_id"] == "42"


# ------------------------------------------------------------------- sort
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
    rows = [{"a": 2}, {"b": 1}, {"a": 1}]
    result = await SortRecordsProcessor()(records=rows, key="a")
    assert [r.get("a") for r in result.data["records"]] == [1, 2, None]


@pytest.mark.asyncio
async def test_sort_without_a_key_is_refused():
    assert (await SortRecordsProcessor()(records=ROWS, key="")).success is False


# --------------------------------------------------------------- purity
@pytest.mark.asyncio
@pytest.mark.parametrize("processor, kwargs", [
    (SelectFieldsProcessor(), {"fields": ["a"]}),
    (HeadProcessor(), {"n": 1}),
    (RenameFieldsProcessor(), {"mapping": {"a": "alpha"}}),
    (FilterRowsProcessor(), {"field": "a", "op": "gt", "value": 1}),
    (SortRecordsProcessor(), {"key": "a", "descending": True}),
    (DeriveReturnProcessor(), {"field": "a"}),
    (ToEvalRecordsProcessor(), {}),
])
async def test_a_transform_never_mutates_its_input(processor, kwargs):
    rows = [dict(row) for row in ROWS]
    await processor(records=rows, **kwargs)
    assert rows == ROWS


@pytest.mark.asyncio
@pytest.mark.parametrize("processor, kwargs", [
    (SelectFieldsProcessor(), {"fields": ["a"]}),
    (HeadProcessor(), {}),
    (RenameFieldsProcessor(), {"mapping": {"a": "b"}}),
    (FilterRowsProcessor(), {"field": "a"}),
    (SortRecordsProcessor(), {"key": "a"}),
    (DeriveReturnProcessor(), {}),
    (ToEvalRecordsProcessor(), {}),
])
async def test_an_empty_batch_flows_through_every_transform(processor, kwargs):
    result = await processor(records=[], **kwargs)
    assert result.success is True
    assert result.data["records"] == []


# ------------------------------------------------------------------ manager
class Doubler(Processor):
    name: str = "doubler"
    description: str = "Doubles a number"

    async def __call__(self, value: int = 0, **kwargs):
        return Response(type=ResponseType.TOOL, success=True, message="ok",
                        data={"value": value * 2})


class Exploding(Processor):
    name: str = "boom"

    async def __call__(self, **kwargs):
        raise RuntimeError("bad transform")


@pytest.fixture
def process():
    return ProcessManager()


@pytest.mark.asyncio
async def test_a_registered_processor_can_be_invoked_by_name(process):
    await process.register(Doubler())
    assert (await process("doubler", {"value": 21})).data["value"] == 42


@pytest.mark.asyncio
async def test_registering_the_same_name_twice_is_refused_by_default(process):
    await process.register(Doubler())
    with pytest.raises(ValueError, match="already registered"):
        await process.register(Doubler())
    await process.register(Doubler(), override=True)  # explicit override is allowed


@pytest.mark.asyncio
async def test_an_unknown_processor_is_refused_by_name(process):
    result = await process("nonesuch", {})
    assert result.success is False
    assert "nonesuch" in result.message


@pytest.mark.asyncio
async def test_a_bad_transform_is_a_failed_result_not_a_crash(process):
    """A processor blowing up must not take the workflow run with it."""
    await process.register(Exploding())
    result = await process("boom", {})
    assert result.success is False
    assert "bad transform" in result.message


@pytest.mark.asyncio
async def test_the_registry_is_listable_and_clearable(process):
    await process.register(Doubler())
    assert await process.list() == ["doubler"]
    assert (await process.get("doubler")).name == "doubler"
    assert await process.get("ghost") is None
    await process.cleanup()
    assert await process.list() == []


@pytest.mark.asyncio
async def test_a_processor_without_a_call_body_is_refused():
    with pytest.raises(NotImplementedError):
        await Processor(name="bare")()


def test_a_process_context_needs_nothing_to_be_constructed():
    assert ProcessContext().input == {}
