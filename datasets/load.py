#!/usr/bin/env python3
"""Fetch and inspect the datasets this repository's benchmarks read.

Every benchmark resolves its data through `ensure_dataset`, which downloads into
`datasets/<name>/` on first use. That makes a run self-healing but also makes the data
invisible until something runs — and the two SWE-bench sets used to bypass it entirely,
landing in the HuggingFace cache where neither `du` nor a code search would find them.

This script is the direct way to that same store: fetch a dataset before a run rather
than during one, or check what is already on disk and how many instances it holds.

    python datasets/load.py --list
    python datasets/load.py swebench_verified swebench_pro
    python datasets/load.py --all

Downloads are idempotent: a dataset with content is reported and left alone.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, NamedTuple, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Source(NamedTuple):
    """One dataset: where it comes from, where it lands, and how big it should be."""

    directory: str          # under datasets/
    hf_repo_id: str         # HuggingFace repo, or "" when it is not fetchable from there
    split: str              # split to count after loading, or "" to count directories
    expected: Optional[int] # published instance count, or None when the set is not fixed
    note: str = ""
    config: str = ""        # HuggingFace config, for repos that ship more than one
    data_files: str = ""    # glob, for repos whose files are not a recognised split layout


#: Keyed by the benchmark name registered in `agentevolver/benchmark/default/`, so a row
#: here and a benchmark there are obviously the same thing.
SOURCES: Dict[str, Source] = {
    "aime24": Source("AIME24", "HuggingFaceH4/aime_2024", "train", 30,
                     data_files="*.parquet"),
    "aime25": Source("AIME25", "yentinglin/aime_2025", "train", 30,
                     data_files="*.jsonl"),
    "gpqa": Source("GPQA", "Idavidrein/gpqa", "train", 448,
                   "Gated on HuggingFace — needs HF_TOKEN in .env.",
                   data_files="gpqa_diamond.csv"),
    "gsm8k": Source("gsm8k", "openai/gsm8k", "test", 1319, config="main"),
    "hle": Source("hle", "cais/hle", "test", 2500),
    "deepweb": Source("deepweb-bench", "", "", None,
                      "Ships with the repository; not fetched from HuggingFace."),
    "programbench": Source("ProgramBench-Tests", "programbench/ProgramBench-Tests", "", 200,
                           "Per-branch test blobs (~8 GB), one directory per instance."),
    "swebench_verified": Source("SWE-bench_Verified", "SWE-bench/SWE-bench_Verified", "test", 500),
    "swebench_pro": Source("SWE-bench_Pro", "ScaleAI/SWE-bench_Pro", "test", 731),
    # Listed so that looking for it finds the reason rather than nothing. Cognition
    # states they "don't currently plan to release the tasks publicly to avoid
    # contamination" and instead evaluate submitted models themselves; Epoch AI's page
    # sources its numbers from Cognition's leaderboard rather than running the set. So
    # there is no download, no harness, and no schema to write a loader against.
    "frontiercode": Source("FrontierCode", "", "test", 150,
                           "Not public — Cognition evaluates submitted models; tasks are "
                           "withheld to avoid contamination. 150 tasks (Main=100)."),
}


def _root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _present(directory: str) -> bool:
    """Whether the dataset is really on disk — the same test `ensure_dataset` applies.

    Asking it a second way here is how a directory ends up "present" to one and "missing"
    to the other, which is the difference between a download that retries and one that
    never does.
    """
    from agentevolver.benchmark.utils import _dir_has_content

    return _dir_has_content(os.path.join(_root(), directory))


def _count(source: Source) -> Tuple[Optional[int], str]:
    """Instances on disk, plus why they could not be counted when they could not.

    The reason is returned rather than swallowed: a bare "?" beside a dataset says
    nothing about whether it is absent, gated, or simply laid out in a way this counter
    does not recognise, and those need different responses from whoever is reading.

    ProgramBench is a directory per instance rather than a split, so it is counted as
    directories; anything with a split is loaded and measured.
    """
    path = os.path.join(_root(), source.directory)
    if not source.split:
        return sum(
            1 for entry in os.listdir(path)
            if os.path.isdir(os.path.join(path, entry)) and not entry.startswith(".")
        ), ""
    try:
        from datasets import load_dataset

        kwargs = {"split": source.split}
        if source.config:
            kwargs["name"] = source.config
        if source.data_files:
            kwargs["data_files"] = source.data_files
        return len(load_dataset(path, **kwargs)), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:70]}"


def fetch(name: str, source: Source) -> bool:
    """Ensure one dataset is on disk. True if it is there when this returns."""
    if _present(source.directory):
        print(f"  ✔ {name}: already at datasets/{source.directory}")
        return True
    if not source.hf_repo_id:
        print(f"  ✘ {name}: not on disk and not fetchable — {source.note}")
        return False

    from agentevolver.benchmark.utils import ensure_dataset

    print(f"  ↓ {name}: downloading {source.hf_repo_id} → datasets/{source.directory}")
    try:
        ensure_dataset(source.directory, source.hf_repo_id)
    except Exception as exc:            # a gated repo, no network, no token
        print(f"  ✘ {name}: {type(exc).__name__}: {exc}")
        return False
    return True


def report(name: str, source: Source) -> None:
    """One line per dataset: where it is, how many instances, whether that is expected."""
    if not _present(source.directory):
        state = "missing"
        if source.note:
            state += f" — {source.note}"
        print(f"  {name:20} {state}")
        return
    found, why = _count(source)
    if found is None:
        print(f"  {name:20} datasets/{source.directory:24} uncountable — {why}")
        return
    shown = str(found)
    verdict = ""
    if source.expected is not None and found is not None and found != source.expected:
        # Worth saying out loud: a short split is how a partial download looks, and it
        # would otherwise show up much later as a benchmark that quietly scored fewer
        # instances than the number it is compared against.
        verdict = f"  ⚠ expected {source.expected}"
    print(f"  {name:20} datasets/{source.directory:24} {shown:>6} instance(s){verdict}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("names", nargs="*", help="Benchmark names to fetch (default: report only).")
    parser.add_argument("--all", action="store_true", help="Fetch every fetchable dataset.")
    parser.add_argument("--list", action="store_true", help="Report what is on disk and exit.")
    args = parser.parse_args()

    unknown = [n for n in args.names if n not in SOURCES]
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(unknown)}. "
                     f"Known: {', '.join(sorted(SOURCES))}")

    if args.list or (not args.names and not args.all):
        print("Datasets on disk:")
        for name, source in SOURCES.items():
            report(name, source)
        return 0

    wanted = sorted(SOURCES) if args.all else args.names
    print("Fetching:")
    ok = all(fetch(name, SOURCES[name]) for name in wanted)
    print("\nDatasets on disk:")
    for name in wanted:
        report(name, SOURCES[name])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
