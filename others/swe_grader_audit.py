"""Host-only grading controls. Never publishes scores or calls a solver/LLM.

Run with ``python -m others.swe_grader_audit --help``. Reference patches stay in
memory and in disposable grading containers, outside every solver workspace.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from pathlib import Path

import pyarrow.parquet as pq

from agentevolver.benchmark import benchmark_manager
from agentevolver.benchmark.types import Task
from agentevolver.benchmark.default.swebench import SWEBenchProBenchmark, grader_fingerprint

from examples.run_swebench_pro import (
    DEFAULT_GRADER_REPO,
    atomic_write_json,
    load_submission,
)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--out", required=True, help="New host-only audit directory")
    parser.add_argument("--dataset", default="datasets/SWE-bench_Pro/data/test-00000-of-00001.parquet")
    parser.add_argument("--grader-repo", default=DEFAULT_GRADER_REPO)
    parser.add_argument("--profile", choices=("official", "diagnostic"), default="official")
    parser.add_argument("--submission", help="Optional frozen candidate; otherwise use dataset reference")
    parser.add_argument("--without-reference-fixtures", action="store_true",
                        help="Control: omit reference testdata changes, retaining all production code")
    args = parser.parse_args()
    if args.submission and args.without_reference_fixtures:
        parser.error("fixture ablation is only supported for the reference, never a candidate")
    rows = pq.read_table(args.dataset, filters=[("instance_id", "=", args.task_id)]).to_pylist()
    if len(rows) != 1:
        parser.error("task-id must identify exactly one dataset row")
    row = rows[0]
    patch = (load_submission(args.submission, args.task_id, row["base_commit"])["patch"]
             if args.submission else row["patch"])
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
    manifest = {
        "instance_id": args.task_id,
        "control": "candidate" if args.submission else "reference",
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "grader_profile": args.profile,
        "grader_fingerprint": grader_fingerprint(args.grader_repo),
        "updates_benchmark_scores": False,
        "omitted_reference_fixtures": omitted,
    }
    atomic_write_json(str(out / "manifest.json"), manifest)
    print(f"Auditing {args.task_id}: {manifest['control']} / {args.profile}", flush=True)
    benchmark = SWEBenchProBenchmark(base_dir=str(out))
    benchmark._data_records = [row]
    await benchmark_manager.register(benchmark, override=True)
    evaluated = await benchmark_manager.eval("swebench_pro", Task(
        task_id=args.task_id,
        result={"instance_id": args.task_id, "base_commit": row["base_commit"],
                "patch": patch, "sha256": manifest["patch_sha256"]},
        extra={"workspace_dir": str(out / "workspace"), "grader_repo": args.grader_repo,
               "grader_profile": args.profile}))
    report = evaluated.evaluation.details
    atomic_write_json(str(out / "report.json"), report)
    print(report, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
