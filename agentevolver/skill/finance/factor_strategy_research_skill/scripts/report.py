"""Validate and render one research report from hash-bound numerical artifacts.

No market download, model call, backtest or adoption happens here. Environments own
the numbers; this adapter resolves their JSON pointers and supplies a working UI.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil


def pointer(value, path):
    if path == "":
        return value
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"Invalid JSON pointer: {path!r}")
    for key in path[1:].split("/"):
        key = key.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def required_text(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing nonempty {key}")
    return value


def id_list(record, key):
    values = record.get(key, [])
    if (not isinstance(values, list)
            or any(not isinstance(v, str) or not v.strip() for v in values)
            or len(set(values)) != len(values)):
        raise ValueError(f"{key} must contain unique nonempty IDs")
    return values


def compile_research(spec, collections, resolve):
    """Preserve open research metadata and check references, not financial judgments."""
    candidates = {c["id"]: c for rows in collections.values() for c in rows}
    kinds = {c["id"]: kind for kind, rows in collections.items() for c in rows}
    strategies = {c["id"] for c in collections["strategies"]}
    for candidate in candidates.values():
        cid = candidate["id"]
        for parent in candidate["parent_ids"]:
            if parent not in candidates or kinds[parent] != kinds[cid]:
                raise ValueError(f"Unknown or incompatible parent: {cid}/{parent}")
        if kinds[cid] == "factors":
            if not set(candidate["qualified_strategy_ids"]).issubset(strategies):
                raise ValueError(f"Unknown qualification consumer: {cid}")
            if (candidate["status"] == "admitted" and candidate["role"] != "return_prediction"
                    and not candidate["qualified_strategy_ids"]):
                raise ValueError(f"Supporting factor needs scoped qualification: {cid}")
            for sid in candidate["qualified_strategy_ids"]:
                if cid not in candidates[sid]["factor_ids"]:
                    raise ValueError(f"Qualification consumer does not bind factor: {cid}/{sid}")

    visited, visiting = set(), set()

    def visit(cid):
        if cid in visiting:
            raise ValueError("Candidate lineage contains a cycle")
        if cid in visited:
            return
        visiting.add(cid)
        for parent in candidates[cid]["parent_ids"]:
            visit(parent)
        visiting.remove(cid)
        visited.add(cid)

    for cid in candidates:
        visit(cid)

    routes, route_ids, covered = [], set(), set()
    for raw in spec.get("routes", []):
        rid = required_text(raw, "id")
        if rid in route_ids:
            raise ValueError(f"Duplicate route: {rid}")
        route_ids.add(rid)
        route = {key: required_text(raw, key) for key in
                 ("id", "hypothesis", "status", "diagnosis", "next_step")}
        if route["status"] not in ("proposed", "active", "retained", "parked", "rejected", "closed"):
            raise ValueError(f"Unknown route status: {rid}")
        for key, kind in (("factor_ids", "factors"), ("strategy_ids", "strategies")):
            route[key] = id_list(raw, key)
            if any(cid not in candidates or kinds[cid] != kind for cid in route[key]):
                raise ValueError(f"Unknown route member: {rid}/{key}")
            covered.update(route[key])
        routes.append(route)
    if not routes or any(c["id"] not in covered for c in candidates.values() if not c["baseline"]):
        raise ValueError("Every research candidate needs a route; baselines are exempt")

    comparisons, comparison_ids = [], set()
    route_members = {r["id"]: set(r["factor_ids"] + r["strategy_ids"]) for r in routes}
    for raw in spec.get("comparisons", []):
        row = {key: required_text(raw, key) for key in
               ("id", "route_id", "parent_id", "candidate_id", "diagnosis", "decision")}
        if row["id"] in comparison_ids:
            raise ValueError("Duplicate comparison ID")
        comparison_ids.add(row["id"])
        parent, child = row["parent_id"], row["candidate_id"]
        if (parent not in candidates or child not in candidates or parent == child
                or kinds[parent] != kinds[child]):
            raise ValueError("Comparison requires distinct candidates of the same kind")
        if any(candidates[cid]["status"] not in ("evaluated", "admitted", "rejected") for cid in (parent, child)):
            raise ValueError("Comparison requires executed candidate results")
        if child not in route_members.get(row["route_id"], set()):
            raise ValueError("Comparison candidate must belong to its route")
        metrics = []
        for metric in raw.get("metrics", []):
            item = {key: required_text(metric, key) for key in ("label", "definition", "unit", "split")}
            if item["split"] not in ("train", "validation", "test"):
                raise ValueError("Invalid comparison split")
            if item["split"] == "test" and spec["test_state"] != "evaluated":
                raise ValueError("Sealed test comparisons forbidden")
            values = {side: resolve(metric[side]) for side in ("parent", "candidate")}
            matched = {}
            for side, cid in (("parent", parent), ("candidate", child)):
                matches = [m for m in candidates[cid]["metrics"]
                           if all(m[k] == metric[side][k] for k in ("source", "pointer"))
                           and all(m[k] == item[k] for k in ("label", "split", "unit"))]
                if len(matches) != 1:
                    raise ValueError("Comparison must unambiguously reference its candidate metric with matching label/split/unit")
                matched[side] = matches[0]
            if matched["parent"]["definition"] != matched["candidate"]["definition"]:
                raise ValueError("Comparison metrics must have the same definition and evaluation convention")
            if not all(finite(v) for v in values.values()):
                raise ValueError("Paired comparison needs two finite measured values")
            item.update(parent=values["parent"], candidate=values["candidate"],
                        delta=values["candidate"] - values["parent"],
                        evidence={side: {k: metric[side][k] for k in ("source", "pointer")}
                                  for side in values})
            if not finite(item["delta"]):
                raise ValueError("Nonfinite comparison delta")
            metrics.append(item)
        if not metrics:
            raise ValueError("Comparison needs measured metrics; retain pending work in route diagnosis")
        comparisons.append(row | {"metrics": metrics})
    return {"routes": routes, "comparisons": comparisons}


def compile_report(path, *, allow_synthetic=False, stage="integrated"):
    path = Path(path).resolve()
    spec = json.loads(path.read_text())
    if type(spec.get("schema")) is not int or spec["schema"] not in (1, 2):
        raise ValueError("Expected report schema=1 or 2")
    joint = spec["schema"] == 2
    if not spec.get("study_id") or not spec.get("data_basis"):
        raise ValueError("study_id and an explicit data_basis are required")
    if spec.get("scope") not in ("research", "synthetic"):
        raise ValueError("scope must be research or synthetic")
    if spec["scope"] == "synthetic" and not allow_synthetic:
        raise ValueError("Synthetic evidence is only allowed with --allow-synthetic for engineering checks")
    if spec.get("test_state") not in ("sealed", "evaluated"):
        raise ValueError("test_state must be sealed or evaluated")
    if stage not in ("factors", "integrated"):
        raise ValueError("Unknown report stage")
    qualification = spec.get("strict_data", {})
    if qualification.get("status") not in ("met", "unmet"):
        raise ValueError("strict_data.status must explicitly be met or unmet")
    reasons = qualification.get("reasons", [])
    if not isinstance(reasons, list) or any(not isinstance(reason, str) or not reason.strip() for reason in reasons):
        raise ValueError("strict_data.reasons must be a list of nonempty strings")
    if qualification["status"] == "unmet" and not qualification.get("reasons"):
        raise ValueError("Unmet strict data requirements need reasons")
    qualification = {"status": qualification["status"], "reasons": qualification.get("reasons", [])}
    sources = {}
    hashes = {}
    for name, ref in spec.get("sources", {}).items():
        source = (path.parent / ref["path"]).resolve()
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != ref["sha256"]:
            raise ValueError(f"Source hash mismatch: {name}")
        payload = json.loads(raw)
        if payload.get("scope") != spec["scope"]:
            raise ValueError(f"Source scope mismatch: {name}")
        sources[name] = payload
        hashes[name] = digest
    if not sources:
        raise ValueError("No numerical artifacts: publish a concise blocker, not an empty research report")

    def resolve(ref):
        return pointer(sources[ref["source"]], ref["pointer"])

    all_ids = set()
    collections = {}
    for kind in ("factors", "strategies"):
        rows = []
        for candidate in spec.get(kind, []):
            cid = required_text(candidate, "id")
            if cid in all_ids:
                raise ValueError(f"Duplicate candidate ID: {cid}")
            all_ids.add(cid)
            definition = candidate.get("formula" if kind == "factors" else "rules")
            if not definition:
                raise ValueError(f"Missing executable definition/rules: {cid}")
            state = candidate.get("status")
            if state not in ("proposed", "blocked", "error", "evaluated", "admitted", "rejected"):
                raise ValueError(f"Unknown candidate status: {cid}")
            metrics = []
            for metric in candidate.get("metrics", []):
                if not metric.get("definition") or not metric.get("unit"):
                    raise ValueError(f"Metric needs its definition and unit: {cid}")
                value = resolve(metric)
                if value is not None and not finite(value):
                    raise ValueError(f"Non-numeric metric: {cid}/{metric['label']}")
                if value is None and not metric.get("reason"):
                    raise ValueError(f"Null metric needs a reason: {cid}/{metric['label']}")
                split = metric["split"]
                if split not in ("train", "validation", "test"):
                    raise ValueError(f"Invalid metric split: {split}")
                if split == "test" and spec.get("test_state") != "evaluated":
                    raise ValueError("Sealed test values must not enter the report payload")
                metrics.append({k: metric.get(k) for k in ("label", "unit", "definition", "split", "reason", "source", "pointer")} | {"value": value})
            measured = any(finite(m["value"]) for m in metrics)
            if state in ("evaluated", "admitted", "rejected") and not measured:
                raise ValueError(f"Evaluated candidate has no measured metrics: {cid}")
            if state in ("proposed", "blocked", "error") and measured:
                raise ValueError(f"Unexecuted candidate cannot carry measured results: {cid}")
            rows.append({"id": cid, "name": candidate.get("name", cid), "definition": definition,
                         "status": state, "reason": candidate.get("reason", ""),
                         "baseline": candidate.get("baseline") is True,
                         "factor_ids": id_list(candidate, "factor_ids"), "metrics": metrics})
            if joint:
                row = rows[-1]
                row.update(family=required_text(candidate, "family"),
                           hypothesis=required_text(candidate, "hypothesis"),
                           parent_ids=id_list(candidate, "parent_ids"),
                           research_only=candidate.get("research_only") is True)
                if row["research_only"] and state == "admitted":
                    raise ValueError(f"Research-only candidate cannot be admitted: {cid}")
                if kind == "factors":
                    row.update(role=required_text(candidate, "role"),
                               qualified_strategy_ids=id_list(candidate, "qualified_strategy_ids"))
                else:
                    roles = candidate.get("factor_roles", {})
                    if (not isinstance(roles, dict) or set(roles) != set(row["factor_ids"])
                            or any(not isinstance(v, str) or not v.strip() for v in roles.values())):
                        raise ValueError(f"Every factor binding needs its role: {cid}")
                    row["factor_roles"] = roles
        collections[kind] = rows
    factors = {c["id"]: c for c in collections["factors"]}
    for strategy in collections["strategies"]:
        if (not strategy["factor_ids"] and not strategy["baseline"]) or any(fid not in factors for fid in strategy["factor_ids"]):
            raise ValueError(f"Unknown or missing factor binding: {strategy['id']}")
        if strategy["status"] in ("evaluated", "admitted", "rejected"):
            allowed = ("evaluated", "admitted", "rejected") if joint and strategy["research_only"] else ("evaluated", "admitted")
            if any(factors[fid]["status"] not in allowed for fid in strategy["factor_ids"]):
                raise ValueError("A strategy cannot silently consume rejected/unexecuted factors")
        if joint:
            for fid in strategy["factor_ids"]:
                factor = factors[fid]
                if strategy["factor_roles"][fid] != factor["role"]:
                    raise ValueError(f"Factor role mismatch: {strategy['id']}/{fid}")
                consumers = factor["qualified_strategy_ids"]
                qualified = factor["status"] == "admitted" and (not consumers or strategy["id"] in consumers)
                if strategy["status"] == "admitted":
                    if factor["status"] != "admitted":
                        raise ValueError("Eligible strategy needs admitted factor versions")
                    if consumers and strategy["id"] not in consumers:
                        raise ValueError(f"Factor qualification does not cover strategy: {strategy['id']}/{fid}")
                elif strategy["status"] in ("evaluated", "rejected") and not qualified and not strategy["research_only"]:
                    raise ValueError("Unqualified factor use must be explicitly research_only")
    research = compile_research(spec, collections, resolve) if joint else {}
    required = ("factors", "strategies") if stage == "integrated" else ("factors",)
    for kind in required:
        if not any(c["status"] in ("evaluated", "admitted", "rejected") for c in collections[kind]):
            raise ValueError(f"No measured {kind}; this is not a {stage} research release")

    charts = []
    chart_ids = set()
    for chart in spec.get("charts", []):
        if chart["id"] in chart_ids:
            raise ValueError("Duplicate chart ID")
        chart_ids.add(chart["id"])
        if chart["kind"] not in ("line", "bar") or chart["section"] not in collections:
            raise ValueError("Charts need kind=line/bar and section=factors/strategies")
        if chart["split"] not in ("train", "validation", "test"):
            raise ValueError("Invalid chart split")
        if chart["split"] == "test" and spec.get("test_state") != "evaluated":
            raise ValueError("Sealed test chart forbidden")
        series = []
        for ref in chart["series"]:
            values = resolve(ref)
            points = [{"x": pointer(row, ref["x"]), "y": pointer(row, ref["y"])} for row in values]
            if any(p["y"] is not None and not finite(p["y"]) for p in points):
                raise ValueError("Chart ordinates must be finite numbers or null gaps")
            if any(not isinstance(p["x"], (str, int, float)) or isinstance(p["x"], bool) for p in points):
                raise ValueError("Chart x values must be dates, categories or numbers")
            if any(isinstance(p["x"], (int, float)) and not finite(p["x"]) for p in points):
                raise ValueError("Chart abscissae must be finite")
            if sum(finite(p["y"]) for p in points) < (2 if chart["kind"] == "line" else 1):
                raise ValueError(f"Chart has no usable measured series: {chart['id']}")
            if chart["kind"] == "line":
                xs = [p["x"] for p in points]
                if xs != sorted(set(xs)):
                    raise ValueError("Line timestamps must be unique and chronological")
            series.append({"label": ref["label"], "source": ref["source"], "points": points})
        if not series:
            raise ValueError("Empty chart series")
        charts.append({k: chart[k] for k in ("id", "title", "kind", "section", "split", "x_label", "y_label")} | {"series": series})
    for kind in required:
        if not any(c["section"] == kind for c in charts):
            raise ValueError(f"No measured chart for {kind}")
    return {"schema": spec["schema"], "study_id": spec["study_id"], "title": spec.get("title", "Signal Foundry"),
            "scope": spec["scope"], "data_basis": spec["data_basis"], "strict_data": qualification,
            "test_state": spec.get("test_state", "sealed"), "sources": hashes,
            "summary": spec.get("summary", ""), **collections, **research, "charts": charts}


def render(report, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    skill = Path(__file__).resolve().parents[1]
    # Copy canonical visual styles into the deployable product; never link host paths.
    import agentevolver
    visual = Path(agentevolver.__file__).resolve().parent / "visual" / "benchmark" / "style.css"
    shutil.copyfile(visual, output / "visual.css")
    for name in ("index.html", "report.js", "report.css"):
        shutil.copyfile(skill / "assets" / name, output / name)
    (output / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    with (output / "metrics.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["stage", "candidate", "metric", "split", "value", "unit", "definition", "reason"])
        for kind in ("factors", "strategies"):
            for candidate in report[kind]:
                for metric in candidate["metrics"]:
                    writer.writerow([kind, candidate["id"], *[metric.get(k) for k in
                                     ("label", "split", "value", "unit", "definition", "reason")]])
    with (output / "series.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["chart", "stage", "split", "series", "x", "y"])
        for chart in report["charts"]:
            for series in chart["series"]:
                for point in series["points"]:
                    writer.writerow([chart["id"], chart["section"], chart["split"], series["label"], point["x"], point["y"]])
    with (output / "comparisons.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["comparison", "route", "parent_id", "candidate_id", "metric", "split",
                         "parent_value", "candidate_value", "delta", "unit", "definition"])
        for comparison in report.get("comparisons", []):
            for metric in comparison["metrics"]:
                writer.writerow([comparison[k] for k in ("id", "route_id", "parent_id", "candidate_id")]
                                + [metric[k] for k in ("label", "split", "parent", "candidate", "delta", "unit", "definition")])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "render"])
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--stage", choices=["factors", "integrated"], default="integrated")
    parser.add_argument("--allow-synthetic", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = compile_report(args.manifest, allow_synthetic=args.allow_synthetic, stage=args.stage)
        if args.action == "render":
            if args.output is None:
                parser.error("render requires --output")
            render(report, args.output)
        print(json.dumps({"ok": True, "scope": report["scope"], "factors": len(report["factors"]),
                          "strategies": len(report["strategies"]), "charts": len(report["charts"])}))
    except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
        parser.exit(1, f"Report is not ready: {error}\n")


if __name__ == "__main__":
    main()
