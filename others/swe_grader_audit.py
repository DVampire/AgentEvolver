"""Host-only grading controls. Never publishes scores or calls a solver/LLM.

Run with ``python -m others.swe_grader_audit --help``. Reference patches stay in
memory and in disposable grading containers, outside every solver workspace.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

import pyarrow.parquet as pq

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentevolver.benchmark import benchmark_manager
from agentevolver.benchmark.types import Task
from agentevolver.utils.file_utils import atomic_json_update



async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--out", required=True, help="New host-only audit directory")
    parser.add_argument("--dataset", default="datasets/SWE-bench_Pro/data/test-00000-of-00001.parquet")
    parser.add_argument("--grader-repo", default=str(Path(__file__).resolve().parents[1] / "others/SWE-bench_Pro"))
    parser.add_argument("--profile", choices=("official", "diagnostic"), default="official")
    parser.add_argument("--submission", help="Optional frozen candidate; otherwise use dataset reference")
    parser.add_argument("--without-reference-fixtures", action="store_true",
                        help="Control: omit reference testdata changes, retaining all production code")
    args = parser.parse_args(argv)
    if args.submission and args.without_reference_fixtures:
        parser.error("fixture ablation is only supported for the reference, never a candidate")
    rows = pq.read_table(args.dataset, filters=[("instance_id", "=", args.task_id)]).to_pylist()
    if len(rows) != 1:
        parser.error("task-id must identify exactly one dataset row")
    row = rows[0]
    candidate = json.loads(Path(args.submission).read_text(encoding="utf-8")) if args.submission else None
    patch = candidate["patch"] if candidate is not None else row["patch"]
    omitted = []
    if args.without_reference_fixtures:
        kept = []
        for section in re.split(r"(?=^diff --git )", patch, flags=re.M):
            header = section.splitlines()[0] if section else ""
            if re.match(r"diff --git a/\S*/testdata/\S+ b/\S*/testdata/\S+$", header):
                omitted.append(header)
            else:
                kept.append(section)
        patch = "".join(kept)
    out = Path(args.out).resolve()
    # No resume/overwrite: each control has independent immutable evidence.
    out.mkdir(parents=True, exist_ok=False)
    await benchmark_manager.configure("swebench_pro", base_dir=str(out), path=args.dataset)
    info = await benchmark_manager.get_info("swebench_pro", evaluation_options={
        "grader_repo": args.grader_repo, "grader_profile": args.profile})
    manifest = {
        "instance_id": args.task_id,
        "control": "candidate" if args.submission else "reference",
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        **info.evaluation,
        "updates_benchmark_scores": False,
        "omitted_reference_fixtures": omitted,
    }
    atomic_json_update(out / "manifest.json", lambda _: manifest)
    print(f"Auditing {args.task_id}: {manifest['control']} / {args.profile}", flush=True)
    evaluated = await benchmark_manager.eval("swebench_pro", Task(
        task_id=args.task_id,
        result=candidate if candidate is not None else {
            "instance_id": args.task_id, "base_commit": row["base_commit"],
            "patch": patch, "sha256": manifest["patch_sha256"]},
        extra={"workspace_dir": str(out / "workspace"), "grader_repo": args.grader_repo,
               "grader_profile": args.profile}))
    report = evaluated.evaluation.details
    atomic_json_update(out / "report.json", lambda _: report)
    print(report, flush=True)

    return 0 if evaluated.evaluation.status != "error" else 1


if __name__ == "__main__":
    import signal

    def _terminate_via_exception(signum, _frame):
        raise KeyboardInterrupt(f"terminated by signal {signum}")

    signal.signal(signal.SIGTERM, _terminate_via_exception)
    sys.exit(asyncio.run(main()))
