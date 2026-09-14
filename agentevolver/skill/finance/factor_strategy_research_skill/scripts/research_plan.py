"""Validate agent-designed chronological windows without choosing research thresholds."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def session(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Sessions must be ISO YYYY-MM-DD dates")
    return value


def interval(value):
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("An interval needs inclusive start/end dates")
    start, end = map(session, value)
    if start > end:
        raise ValueError("Reversed interval")
    return start, end


def validate_plan(study, plan, sessions):
    """Check every window against a verified TRAIN calendar, never infer annual folds.

    The caller obtains sessions from check_snapshot.expected_sessions and verifies
    actual data coverage separately. No prices, results or fitted values are read.
    """
    splits = study["splits"]
    if "validation" in splits:
        raise ValueError("This contract uses train/test; rolling windows belong inside train")
    train_start, train_end = interval(splits["train"])
    test_start, _ = interval(splits["test"])
    if train_end >= test_start:
        raise ValueError("Train and test overlap or are out of order")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError("Supply the verified training session calendar")
    for value in sessions:
        session(value)
    if sessions != sorted(set(sessions)) or sessions[0] < train_start or sessions[-1] > train_end:
        raise ValueError("Calendar must be unique, chronological and inside train")
    if type(plan.get("schema")) is not int or plan["schema"] != 1 or plan.get("study_id") != study["study_id"]:
        raise ValueError("Plan schema/study identity mismatch")
    if plan.get("train_sessions_sha256") != digest(sessions):
        raise ValueError("Plan is not bound to this training calendar")
    windows = plan.get("windows")
    if not isinstance(windows, list) or not windows:
        raise ValueError("Agent must design at least one chronological research window")
    index = {value: i for i, value in enumerate(sessions)}
    ids, scored = set(), set()
    for window in windows:
        name = window.get("id")
        if not isinstance(name, str) or not name.strip() or name in ids:
            raise ValueError("Window IDs must be nonempty and unique")
        ids.add(name)
        fit_start, fit_end = interval(window["fit"])
        score_start, score_end = interval(window["score"])
        if any(d not in index for d in (fit_start, fit_end, score_start, score_end)):
            raise ValueError("Window endpoints must be actual sessions inside train")
        gap = window.get("gap_sessions")
        if type(gap) is not int or gap < 0:
            raise ValueError("Declare a nonnegative session gap based on label availability")
        if index[score_start] - index[fit_end] - 1 < gap:
            raise ValueError("Fit is not strictly before scoring with the declared gap")
        eligible = set(sessions[index[score_start]:index[score_end] + 1])
        if scored & eligible:
            raise ValueError("Scoring windows overlap; use a separate plan/scenario for repeated dates")
        scored |= eligible
    return {"ok": True, "plan_sha256": digest(plan), "window_ids": [w["id"] for w in windows],
            "scored_sessions": len(scored), "selection_scope": "train",
            "test_prior_exposure": study.get("test_policy", {}).get("prior_exposure", "unknown")}


def validate_fit(window, feature_dates, label_exit_dates):
    """Check actual retained rows for factor OR combination fitting after label purging.

    Unsupervised transforms pass each row's availability date as its label exit.
    More complex targets pass the latest availability of any fitted target/input.
    """
    start, end = interval(window["fit"])
    score_start, _ = interval(window["score"])
    if end >= score_start:
        raise ValueError("Fit cutoff must precede scoring")
    if not feature_dates or len(feature_dates) != len(label_exit_dates):
        raise ValueError("Fit receipt needs aligned, nonempty feature/label availability dates")
    for feature, available in zip(feature_dates, label_exit_dates):
        session(feature)
        session(available)
        if not start <= feature <= available <= end:
            raise ValueError("Fitted row or target crosses the allowed training cutoff")
    return {"ok": True, "window_id": window["id"], "rows": len(feature_dates),
            "latest_label_available": max(label_exit_dates),
            "fit_rows_sha256": digest([feature_dates, label_exit_dates])}


def validate_research_dates(study, dates):
    """Call before factor materialization, fitting, simulation and comparison reads."""
    start, end = interval(study["splits"]["train"])
    if not dates:
        raise ValueError("Research request has no sessions")
    if any(not start <= session(value) <= end for value in dates):
        raise ValueError("Ordinary research operations may only consume train")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("sessions", type=Path, help="JSON list of verified train session dates")
    args = parser.parse_args()
    try:
        values = [json.loads(path.read_text()) for path in (args.study, args.plan, args.sessions)]
        print(json.dumps(validate_plan(*values)))
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.exit(1, f"Invalid research plan: {error}\n")


if __name__ == "__main__":
    main()
