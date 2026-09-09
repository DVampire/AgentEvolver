"""Usage accounting must preserve unknowns, identities and historical coverage."""

import json
from urllib.parse import urlencode

import pytest

from agentevolver.trace.usage import normalize_usage
from agentevolver.visual.benchmark.server import usage_sources
from agentevolver.visual.usage.server import UsageView


def write(log, events, mode="w", suffix="\n", name="trace"):
    root = log / "trace"
    root.mkdir(parents=True, exist_ok=True)
    with (root / (name + ".jsonl")).open(mode) as f:
        f.write("\n".join(json.dumps(e) for e in events) + suffix)
    return root / (name + ".jsonl")


def call(identity, **kwargs):
    return dict(
        id=identity,
        session_id="s",
        task_id="p",
        step_number=0,
        agent_name="Builder",
        event_type="agent_call",
        timestamp="2026-09-08T00:00:00Z",
        **kwargs,
    )


def view(log, summary=None):
    return UsageView(lambda: [{"id": "attempt1", "log_root": str(log), "summary": summary}])


def test_calls_cost_and_cache_are_not_double_counted(tmp_path):
    row = call(
        "c1",
        usage={
            "input_tokens": 10,
            "context_input_tokens": 100,
            "cache_read_tokens": 80,
            "cache_write_tokens": 10,
            "output_tokens": 20,
            "reasoning_tokens": 5,
            "cost": 0.1,
            "cost_status": "reported",
        },
    )
    write(tmp_path, [row, call("end", usage=row["usage"])])
    # A copy of the same event is not another call; its different snapshot content
    # is immaterial to billing identity.
    write(tmp_path, [row], name="copy")
    u = view(tmp_path).query()
    assert u["summary"]["calls"] == 2
    assert u["summary"]["total_tokens"] == 240
    assert u["summary"]["cache_hit_ratio"] == 0.8
    assert float(u["summary"]["cost"]) == 0.2
    assert u["coverage"]["legacy_steps"] == 2


def test_request_receipts_include_auxiliary_costs_without_double_counting_steps(tmp_path):
    def receipt(identity, operation, cost, at, success=True):
        return call(identity) | {
            "event_type": "custom", "timestamp": f"2026-09-08T00:00:0{at}Z",
            "success": success, "usage": {"cost": cost, "input_tokens": 100, "output_tokens": 20},
            "metadata": {"type": "model_usage", "model": "model-A", "provider": "p",
                         "operation": operation, "request_snapshot_id": "identical-payload-hash"},
        }
    records = [receipt("retry", "generation", .2, 0, False),
               receipt("compact", "compact", .3, 1),
               receipt("audit", "checkpoint.audit", .4, 2),
               receipt("main", "generation", .5, 3),
               call("step") | {"timestamp": "2026-09-08T00:00:04Z", "usage": {"cost": .5}},
               # A later resident assignment reuses step 0 but has legacy-only telemetry.
               call("later") | {"timestamp": "2026-09-08T00:00:05Z", "usage": {"cost": .6}}]
    write(tmp_path, records)
    result = view(tmp_path).query()
    assert result["summary"]["calls"] == 5
    assert float(result["summary"]["cost"]) == 2.0
    assert result["coverage"]["requests"] == 4
    assert result["coverage"]["legacy_steps"] == 1
    assert result["granularity"] == "mixed"
    compact_only = view(tmp_path).query("operation=compact")
    assert compact_only["summary"]["calls"] == 1
    assert float(compact_only["summary"]["cost"]) == .3
    assert compact_only["series"][0]["operations"] == ["compact"]
    assert "generation" in result["facets"]["operation"]


def test_missing_usage_and_explicit_zero_are_distinct(tmp_path):
    write(
        tmp_path,
        [call("zero", usage={"input_tokens": 0, "output_tokens": 0, "cost": 0}), call("missing")],
    )
    u = view(tmp_path).query()
    assert u["summary"]["calls"] == 2
    assert u["summary"]["costed_calls"] == 1
    assert u["summary"]["token_calls"] == 1
    assert sorted(p["value"] is None for p in u["series"]) == [False, True]
    assert normalize_usage({"output_tokens": 10})["context_input_tokens"] is None


@pytest.mark.parametrize("with_sequences", [True, False])
def test_equal_timestamps_do_not_double_bill_receipts_and_steps(tmp_path, with_sequences):
    records = [
        call("z-receipt", usage={"cost": .5}, seq_no=1) | {
            "event_type": "custom", "metadata": {
                "type": "model_usage", "operation": "generation", "model": "m", "provider": "p"}},
        call("a-step", usage={"cost": .5}, seq_no=2),
    ]
    if not with_sequences:
        for row in records:
            row.pop("seq_no")
    write(tmp_path, records)
    result = view(tmp_path).query()
    assert result["summary"]["calls"] == 1
    assert float(result["summary"]["cost"]) == .5


def test_snapshot_identity_is_not_call_identity_and_no_prompt_leaks(tmp_path):
    req = {
        "event_type": "model_request",
        "session_id": "s",
        "task_id": "p",
        "step_number": 0,
        "input": {
            "snapshot_id": "same",
            "routed_model": "model-A",
            "provider": "provider",
            "messages": ["SECRET"],
        },
    }
    write(
        tmp_path,
        [
            dict(req, id="r1"),
            dict(req, id="r2"),
            call("c", duration_ms=2000, usage={"input_tokens": 1, "output_tokens": 2}),
        ],
    )
    u = view(tmp_path).query()
    assert u["summary"]["calls"] == 1
    assert u["summary"]["request_attempts"] == 2
    assert u["calls"][0]["model"] == "model-A"
    assert u["calls"][0]["latency_ms"] is None
    assert u["calls"][0]["step_duration_ms"] == 2000
    assert "SECRET" not in json.dumps(u)


def test_partial_appends_rotation_and_deletion(tmp_path):
    row = call("a", usage={"cost": 1})
    p = write(tmp_path, [row], suffix="")
    v = view(tmp_path)
    assert v.query()["row_count"] == 0
    with p.open("a") as f:
        f.write("\n")
    assert v.query()["row_count"] == 1
    assert v.query()["row_count"] == 1
    p.write_text("")
    assert v.query()["row_count"] == 0
    write(tmp_path, [row])
    assert v.query()["row_count"] == 1
    p.unlink()
    assert v.query()["row_count"] == 0


def test_summary_is_not_added_to_the_same_trace(tmp_path):
    write(tmp_path, [call("c", usage={"input_tokens": 2, "output_tokens": 3, "cost": 1})])
    summary = {"n_llm_calls": 1, "total_cost_usd": 1}
    u = view(tmp_path, summary).query()
    assert u["summary"]["calls"] == 1
    assert float(u["summary"]["cost"]) == 1
    assert u["historical"]["calls"] == 0
    partial = view(tmp_path, {**summary, "n_llm_calls": 3, "total_cost_usd": 4}).query()
    assert partial["summary"]["calls"] == 3
    assert float(partial["summary"]["cost"]) == 4
    assert partial["series"] == []
    assert partial["historical"]["calls"] == 3


def test_filter_bucket_export_and_unclassified_input(tmp_path):
    rows = [
        call(str(i), usage={"context_input_tokens": 100, "output_tokens": 5, "cost": i / 10})
        for i in range(3)
    ]
    rows[1]["agent_name"] = "Other"
    rows[2]["timestamp"] = "2026-09-08T00:01:00Z"
    write(tmp_path, rows)
    v = view(tmp_path)
    q = urlencode({"agent_name": "Builder", "axis": "time", "bucket": 60, "metric": "tokens"})
    u = v.query(q)
    assert u["summary"]["calls"] == 2
    assert sum(r["value"] for r in u["series"]) == 210
    assert u["series"][0]["tokens"]["unclassified_input"] == 100
    csv = v.query(q + "&view=export")
    assert len(csv.splitlines()) == 3
    assert "Other" not in csv
    bounded = v.query(urlencode({"from": "2026-09-08T00:00:00Z", "to": "2026-09-08T00:01:00Z"}))
    assert bounded["summary"]["calls"] == 2
    with pytest.raises(ValueError):
        v.query("bucket=0&axis=time")
    with pytest.raises(ValueError):
        v.query("view=call&id=not-found")


def test_copied_benchmark_results_keep_distinct_retry_attempts(tmp_path):
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    state = tmp_path / "monitor.json"
    r = {
        "instance_id": "task",
        "session_path": str(tmp_path / "s1"),
        "spend": {"n_llm_calls": 1, "total_cost_usd": 1},
    }
    retry = {
        **r,
        "session_path": str(tmp_path / "s2"),
        "spend": {"n_llm_calls": 1, "total_cost_usd": 2},
    }
    old.write_text(json.dumps([r]))
    new.write_text(json.dumps([r, retry]))
    state.write_text(json.dumps({"results_path": str(new)}))
    (tmp_path / "aggregate.json").write_text(json.dumps({"history": [str(old)]}))
    u = UsageView(lambda: usage_sources(state)).query()
    assert u["summary"]["calls"] == 2
    assert float(u["summary"]["cost"]) == 3
    assert len(u["calls"]) == 2
    assert not u["series"]


def test_resident_turns_reusing_step_number_do_not_reuse_requests(tmp_path):
    request = {
        "event_type": "model_request",
        "session_id": "s",
        "task_id": "p",
        "step_number": 0,
        "input": {"routed_model": "A"},
    }
    first = call("c1", usage={"cost": 1})
    second = {**call("c2", usage={"cost": 2}), "timestamp": "2026-09-08T00:02:00Z"}
    write(
        tmp_path,
        [
            dict(request, id="r1", timestamp="2026-09-07T23:59:00Z"),
            first,
            dict(request, id="r2", timestamp="2026-09-08T00:01:00Z"),
            second,
        ],
    )
    data = view(tmp_path).query()
    assert data["summary"]["request_attempts"] == 2
    assert all(r["request_attempts"] == 1 for r in data["calls"])


def test_corrupt_trace_coverage_warning_survives_refresh(tmp_path):
    path = write(tmp_path, [call("valid", usage={"cost": 1})])
    with path.open("a") as f:
        f.write("{broken json}\n")
    v = view(tmp_path)
    assert v.query()["coverage"]["read_errors"] == 1
    assert v.query()["coverage"]["read_errors"] == 1
    path.unlink()
    assert v.query()["coverage"]["read_errors"] == 0


def complete_tokens(multiplier=1):
    return {
        k: v * multiplier
        for k, v in {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_tokens": 80,
            "cache_write_tokens": 10,
            "context_input_tokens": 100,
            "reasoning_tokens": 5,
        }.items()
    }


@pytest.mark.parametrize("axis", ["call", "time"])
def test_token_dimensions_conserve_totals_and_accumulate_independently(tmp_path, axis):
    rows = [call(str(i), usage=complete_tokens(i + 1)) for i in range(3)]
    rows[1]["timestamp"] = "2026-09-08T00:00:20Z"
    rows[2]["timestamp"] = "2026-09-08T00:01:00Z"
    write(tmp_path, rows)
    v = view(tmp_path)
    query = f"metric=tokens&axis={axis}&bucket=60"
    ordinary = v.query(query)
    accumulated = v.query(query + "&cumulative=true")
    cumulative = {}
    for point, cumulative_point in zip(ordinary["series"], accumulated["series"]):
        t = point["tokens"]
        assert t["cache_tokens"] == t["cache_read_tokens"] + t["cache_write_tokens"]
        assert t["total_tokens"] == t["input_tokens"] + t["output_tokens"] + t["cache_tokens"]
        assert t["total_tokens"] == t["context_input_tokens"] + t["output_tokens"]
        for field, amount in t.items():
            cumulative[field] = cumulative.get(field, 0) + amount
            assert cumulative_point["tokens"][field] == cumulative[field]
        assert cumulative_point["value"] == cumulative["total_tokens"]
    assert cumulative["total_tokens"] == ordinary["summary"]["total_tokens"] == 720
    assert cumulative["reasoning_tokens"] == 30  # A subset, not added to the 720.


def test_token_gaps_and_partial_coverage_survive_cumulative_and_buckets(tmp_path):
    first = call("a", usage=complete_tokens())
    missing = call("b", usage={"output_tokens": 7})
    missing["timestamp"] = "2026-09-08T00:00:10Z"
    zero = call("c", usage=complete_tokens(0))
    zero["timestamp"] = "2026-09-08T00:01:00Z"
    write(tmp_path, [first, missing, zero])
    v = view(tmp_path)
    points = v.query("metric=tokens&cumulative=true")["series"]
    assert points[1]["tokens"]["cache_tokens"] is None
    assert points[1]["tokens"]["total_tokens"] is None
    assert points[1]["tokens"]["output_tokens"] == 27
    assert points[2]["tokens"]["cache_tokens"] == 90
    assert points[2]["token_coverage"]["cache_tokens"] == 2
    assert points[2]["token_observations"] == 3
    assert points[2]["tokens"]["total_tokens"] == 120
    bucket = v.query("metric=tokens&axis=time&bucket=60")["series"][0]
    assert bucket["tokens"]["cache_tokens"] == 90
    assert bucket["tokens"]["output_tokens"] == 27
    assert bucket["token_coverage"]["cache_tokens"] == 1
    assert bucket["token_coverage"]["output_tokens"] == 2
    assert bucket["token_observations"] == 2
    assert v.query("metric=tokens")["series"][2]["tokens"]["cache_tokens"] == 0


def test_conflicting_breakdown_does_not_rewrite_the_recorded_total(tmp_path):
    write(tmp_path, [call("c", usage={**complete_tokens(), "context_input_tokens": 50})])
    point = view(tmp_path).query("metric=tokens")["series"][0]
    assert point["token_conflicts"] == 1
    assert point["tokens"]["unclassified_input"] is None
    assert point["tokens"]["total_tokens"] == 70
    assert point["tokens"]["cache_tokens"] == 90


def test_large_charts_keep_each_dimension_when_grouping_records(tmp_path):
    write(tmp_path, [call(str(i), usage=complete_tokens()) for i in range(801)])
    data = view(tmp_path).query("metric=tokens")
    assert len(data["series"]) <= 750
    for field, amount in {**complete_tokens(), "cache_tokens": 90, "total_tokens": 120}.items():
        assert sum(p["tokens"][field] for p in data["series"]) == amount * 801
    assert sum(p["count"] for p in data["series"]) == 801
