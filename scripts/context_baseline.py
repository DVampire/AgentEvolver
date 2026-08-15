#!/usr/bin/env python3
"""Measure one agent task, so `derive_context` can be judged rather than guessed.

Switching the model's history from the rendered memory transcript to the log's own
projection changes what every step of every agent sees. That is a behavioural change,
not a refactor, and the only honest way to decide it is to run the same task both ways
and compare. This script records one side of that comparison.

    scripts/context_baseline.py --task-file examples/tasks/reverse_string.md --label rendered
    scripts/context_baseline.py --task-file examples/tasks/reverse_string.md --label derived \\
        --cfg-options code_agent.derive_context=True
    scripts/context_baseline.py --compare rendered derived

What it reads is the session's own trace and prompt files, not the agent's self-report:
an agent that says it finished is not evidence that it did.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "output" / "results" / "context"


def _sessions_after(started: float) -> List[Path]:
    """Session directories written by this run."""
    root = ROOT / "output"
    return [d for d in root.glob("*/sessions/*") if d.is_dir() and d.stat().st_mtime >= started]


def _events(session: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in session.glob("log/trace/*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _classify(text: str) -> str:
    """Which context path produced this prompt.

    A derived prompt carries no `<memory>` block at all, because the projection replaces
    the rendered agent-context wholesale. Reading an absent block as "memory was empty"
    is how the first version of this script reported the two paths as the same thing.
    """
    block = re.search(r"<memory>(.*?)</memory>", text, re.S)
    if block is None:
        return "derived"
    body = block.group(1).strip()
    if "is disabled" in body:
        return "rendered/disabled"
    if "unavailable" in body:
        return "rendered/unavailable"
    if "No memory recorded" in body or not body:
        return "rendered/no-history"
    return "rendered/history"


def _body(text: str) -> str:
    """The messages inside a snapshot, without the file wrapper around them.

    The wrapper carries the step number in its `<title>`, so two consecutive snapshots
    diverge 95 characters in no matter what was sent. Comparing whole files measured the
    instrument instead of the prompt — every path scored ~0.1% reuse, which is the
    signature of the wrapper, not of the history.
    """
    start = text.find("<body>")
    end = text.rfind("</body>")
    return text[start + len("<body>"):end] if start != -1 and end != -1 else text


def _prefix_share(earlier: str, later: str) -> float:
    """How much of one prompt survives as a literal prefix of the next.

    The number the whole change is about. A rendered prompt re-renders the entire
    history into one turn every step, so a single earlier byte moving invalidates
    everything after it and a cache can never hit. A derived prompt appends the turns
    that happened, so the earlier bytes stay put. 1.0 means the next request could reuse
    the whole of this one; near 0 means it starts from scratch.
    """
    earlier, later = _body(earlier), _body(later)
    if not earlier:
        return 0.0
    n = min(len(earlier), len(later))
    i = 0
    while i < n and earlier[i] == later[i]:
        i += 1
    return round(i / len(earlier), 3)


def _usage_by_agent(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """What each agent actually spent, from the counts the provider returned.

    Prefix reuse below is a *proxy*: it says a cache could have hit. This says whether
    one did. They are not the same claim and the gap between them is where the money
    goes — a prompt can be 99% reusable and still be billed in full, which is exactly
    what happened while the only cache breakpoint sat on the system message and the
    capability catalogs were rendered after the agent state.

    Steps whose usage the provider did not report are counted separately rather than as
    zero, so a short total cannot be mistaken for a cheap run.
    """
    per_agent: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.get("event_type") != "agent_call":
            continue
        name = row.get("agent_name") or "?"
        entry = per_agent.setdefault(name, {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost": 0.0, "steps_with_usage": 0, "steps_without_usage": 0,
        })
        usage = row.get("usage")
        if not isinstance(usage, dict):
            entry["steps_without_usage"] += 1
            continue
        entry["steps_with_usage"] += 1
        for key in ("input_tokens", "output_tokens", "cache_read_tokens",
                    "cache_write_tokens"):
            entry[key] += int(usage.get(key) or 0)
        entry["cost"] += float(usage.get("cost") or 0.0)

    for entry in per_agent.values():
        billed = entry["input_tokens"]
        # Providers report cached reads as a subset of the input count, so the share is
        # of input — not of input + cache_read, which would understate it.
        entry["cache_hit_rate"] = (round(entry["cache_read_tokens"] / billed, 3)
                                   if billed else None)
    return per_agent


def _measure(session: Path) -> Dict[str, Any]:
    """What one run cost, per agent, and whether its prompts were reusable.

    Per agent because a run may use both paths at once — switching one agent on leaves
    the others rendered, and summing their prompt sizes together buries exactly the
    difference the run was meant to show.
    """
    rows = _events(session)
    usage = _usage_by_agent(rows)
    agents: Dict[str, Dict[str, Any]] = {}
    for path in sorted(session.glob("log/messages/*/*.html")):
        text = path.read_text(encoding="utf-8")
        entry = agents.setdefault(path.parent.name, {"paths": {}, "sizes": [], "texts": []})
        path_type = _classify(text)
        entry["paths"][path_type] = entry["paths"].get(path_type, 0) + 1
        entry["sizes"].append(len(_body(text)))
        entry["texts"].append(text)

    per_agent = {}
    for name, entry in agents.items():
        texts, sizes = entry.pop("texts"), entry["sizes"]
        shares = [_prefix_share(a, b) for a, b in zip(texts, texts[1:])]
        per_agent[name] = {
            "path": max(entry["paths"], key=entry["paths"].get),
            "paths": entry["paths"],
            "prompts": len(sizes),
            "first_chars": sizes[0] if sizes else 0,
            "last_chars": sizes[-1] if sizes else 0,
            "mean_chars": round(sum(sizes) / len(sizes)) if sizes else 0,
            # Mean over consecutive pairs: what a cache could have reused each step.
            "prefix_reuse": round(sum(shares) / len(shares), 3) if shares else None,
            # What it actually reused. Absent when this process recorded no usage for
            # the agent, which is a different fact from "it reused nothing".
            "usage": usage.get(name),
        }

    for name, entry in usage.items():
        # An agent that emitted calls but rendered no prompt snapshots would otherwise
        # vanish from the table along with everything it spent.
        per_agent.setdefault(name, {"path": "?", "paths": {}, "prompts": 0,
                                    "first_chars": 0, "last_chars": 0, "mean_chars": 0,
                                    "prefix_reuse": None, "usage": entry})

    steps = [r.get("step_number") for r in rows if r.get("step_number") is not None]
    return {
        "session": session.name,
        "events": len(rows),
        "tool_calls": sum(1 for r in rows if r.get("event_type") == "tool_start"),
        "max_step": max(steps) + 1 if steps else 0,
        "agents": per_agent,
        "agent_ended_ok": any(
            r.get("event_type") == "agent_end" and r.get("success") for r in rows
        ),
    }


def _one_task(task_file: str, args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    """Run one task and measure the sessions it produced.

    Each task gets its own subprocess and its own session directory, matched by mtime.
    Returns None when nothing was written, which is a failure to *measure*, not a task
    that scored zero — the caller must not average it in as one.
    """
    started = time.time()
    command = [sys.executable, str(ROOT / "examples" / "run_meta_agent.py"),
               "--task-file", task_file]
    if args.cfg_options:
        command += ["--cfg-options", *args.cfg_options]

    print(f"  running: {Path(task_file).name}", flush=True)
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                               timeout=args.timeout)
    sessions = _sessions_after(started)
    if not sessions:
        print(f"  no session for {task_file}; skipped", file=sys.stderr)
        print(completed.stderr[-1500:], file=sys.stderr)
        return None

    measured = [_measure(s) for s in sorted(sessions)]
    for entry in measured:
        entry["task_file"] = task_file
        entry["exit_code"] = completed.returncode
        entry["wall_seconds"] = round(time.time() - started, 1)
    return measured


def run(args: argparse.Namespace) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    tasks = args.task_file if isinstance(args.task_file, list) else [args.task_file]

    runs: List[Dict[str, Any]] = []
    skipped: List[str] = []
    started = time.time()
    for task_file in tasks:
        measured = _one_task(task_file, args)
        if measured is None:
            skipped.append(task_file)
            continue
        runs.extend(measured)

    if not runs:
        print("nothing measured", file=sys.stderr)
        return 1

    record = {
        "label": args.label,
        "task_files": tasks,
        # Named, not counted: "3 of 4 tasks" invites averaging over whichever three
        # happened to work, and a task that could not be measured is not a data point.
        "skipped": skipped,
        "cfg_options": args.cfg_options or [],
        "wall_seconds": round(time.time() - started, 1),
        "runs": runs,
    }
    out = RESULTS / f"{args.label}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nmeasured {len(runs)} run(s) over {len(tasks) - len(skipped)}/{len(tasks)} task(s)")
    if skipped:
        print(f"skipped: {', '.join(skipped)}")
    print(f"written: {out.relative_to(ROOT)}")
    return 0


def compare(labels: List[str]) -> int:
    records = []
    for label in labels:
        path = RESULTS / f"{label}.json"
        if not path.exists():
            print(f"no measurement for {label!r} — run it first", file=sys.stderr)
            return 1
        records.append(json.loads(path.read_text(encoding="utf-8")))

    rows = [("label", "task", "agent", "path", "steps", "prefix reuse",
             "input tok", "cache read", "cache hit")]
    gaps: List[str] = []
    for record in records:
        for run_ in record["runs"]:
            task = Path(run_.get("task_file", "?")).stem[:18]
            for agent, m in sorted(run_["agents"].items()):
                reuse = "—" if m["prefix_reuse"] is None else f"{m['prefix_reuse']:.1%}"
                u = m.get("usage") or {}
                hit = u.get("cache_hit_rate")
                if u.get("steps_without_usage"):
                    gaps.append(f"{record['label']}/{agent}: "
                                f"{u['steps_without_usage']} step(s) reported no usage")
                rows.append((
                    record["label"], task, agent, m["path"], str(m["prompts"]), reuse,
                    f"{u.get('input_tokens', 0):,}" if u else "—",
                    f"{u.get('cache_read_tokens', 0):,}" if u else "—",
                    "—" if hit is None else f"{hit:.1%}",
                ))

    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for i, row in enumerate(rows):
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))
        if i == 0:
            print("  ".join("-" * w for w in widths))

    print()
    # Pooled per label: totals, not a mean of per-run rates. Averaging rates gives a
    # short run the same weight as a long one, and the question is what a session costs.
    for record in records:
        pooled_in = pooled_read = 0
        ok = total = 0
        for run_ in record["runs"]:
            total += 1
            ok += bool(run_["agent_ended_ok"])
            for m in run_["agents"].values():
                u = m.get("usage") or {}
                pooled_in += u.get("input_tokens", 0)
                pooled_read += u.get("cache_read_tokens", 0)
        share = f"{pooled_read / pooled_in:.1%}" if pooled_in else "—"
        print(f"{record['label']:<10} {ok}/{total} runs ended ok   "
              f"pooled input {pooled_in:,}  cache read {pooled_read:,}  ({share})"
              + (f"   skipped: {', '.join(record['skipped'])}"
                 if record.get("skipped") else ""))

    for gap in gaps:
        print(f"  incomplete: {gap}")

    print("\nRead `cache hit` first — it is what the provider billed. `prefix reuse` only "
          "says a cache *could* have hit; the two came apart badly once, when a 99%-"
          "reusable prompt was billed in full because the only breakpoint sat on the "
          "system message. A run where they disagree is a bug in the request, not in the "
          "measurement. The deliverable still has to be checked by hand.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task-file", nargs="+",
                        default=[str(ROOT / "examples" / "tasks" / "reverse_string.md")],
                        help="One or more task files; each is run and measured separately.")
    parser.add_argument("--label", default="rendered", help="Name this measurement.")
    parser.add_argument("--cfg-options", nargs="+", help="Passed through to the runner.")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--compare", nargs="+", metavar="LABEL",
                        help="Print a table of measurements already recorded.")
    args = parser.parse_args()
    return compare(args.compare) if args.compare else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
