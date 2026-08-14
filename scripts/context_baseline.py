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
from typing import Any, Dict, List

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


def _measure(session: Path) -> Dict[str, Any]:
    """What one run cost, per agent, and whether its prompts were reusable.

    Per agent because a run may use both paths at once — switching one agent on leaves
    the others rendered, and summing their prompt sizes together buries exactly the
    difference the run was meant to show.
    """
    rows = _events(session)
    agents: Dict[str, Dict[str, Any]] = {}
    for path in sorted(session.glob("log/messages/*/*.html")):
        text = path.read_text(encoding="utf-8")
        entry = agents.setdefault(path.parent.name, {"paths": {}, "sizes": [], "texts": []})
        kind = _classify(text)
        entry["paths"][kind] = entry["paths"].get(kind, 0) + 1
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
        }

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


def run(args: argparse.Namespace) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    started = time.time()
    command = [sys.executable, str(ROOT / "examples" / "run_meta_agent.py"),
               "--task-file", args.task_file]
    if args.cfg_options:
        command += ["--cfg-options", *args.cfg_options]

    print(f"running: {' '.join(command)}")
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                               timeout=args.timeout)
    elapsed = time.time() - started

    sessions = _sessions_after(started)
    if not sessions:
        print("no session directory was produced; nothing to measure", file=sys.stderr)
        print(completed.stderr[-2000:], file=sys.stderr)
        return 1

    record = {
        "label": args.label,
        "task_file": args.task_file,
        "cfg_options": args.cfg_options or [],
        "exit_code": completed.returncode,
        "wall_seconds": round(elapsed, 1),
        "runs": [_measure(s) for s in sorted(sessions)],
    }
    out = RESULTS / f"{args.label}.json"
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"\nwritten: {out.relative_to(ROOT)}")
    return 0


def compare(labels: List[str]) -> int:
    records = []
    for label in labels:
        path = RESULTS / f"{label}.json"
        if not path.exists():
            print(f"no measurement for {label!r} — run it first", file=sys.stderr)
            return 1
        records.append(json.loads(path.read_text(encoding="utf-8")))

    rows = [("label", "agent", "path", "prompts", "first", "last", "mean", "prefix reuse")]
    for record in records:
        for run_ in record["runs"]:
            for agent, m in sorted(run_["agents"].items()):
                reuse = "—" if m["prefix_reuse"] is None else f"{m['prefix_reuse']:.1%}"
                rows.append((
                    record["label"], agent, m["path"], str(m["prompts"]),
                    f"{m['first_chars']:,}", f"{m['last_chars']:,}",
                    f"{m['mean_chars']:,}", reuse,
                ))

    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for i, row in enumerate(rows):
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))
        if i == 0:
            print("  ".join("-" * w for w in widths))

    print()
    for record in records:
        for run_ in record["runs"]:
            print(f"{record['label']}: exit={record['exit_code']} "
                  f"{record['wall_seconds']}s  steps={run_['max_step']} "
                  f"tools={run_['tool_calls']} ended_ok={run_['agent_ended_ok']}")
    print("\nprefix reuse is the number to read: a rendered prompt re-renders its whole "
          "history every step, so a cache can never hit; a derived one appends. The "
          "deliverable still has to be checked by hand.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task-file", default=str(ROOT / "examples" / "tasks" / "reverse_string.md"))
    parser.add_argument("--label", default="rendered", help="Name this measurement.")
    parser.add_argument("--cfg-options", nargs="+", help="Passed through to the runner.")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--compare", nargs="+", metavar="LABEL",
                        help="Print a table of measurements already recorded.")
    args = parser.parse_args()
    return compare(args.compare) if args.compare else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
