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


class Shape(NamedTuple):
    """What a benchmark class does not carry: how to count it, and what to expect.

    Where the data comes from and where it lands are the benchmark's own fields, read
    straight off the registered class. Restating them here is how this file came to name
    three HuggingFace repos that no benchmark had ever used.
    """

    split: str = "test"     # split to count, or "" to count directories
    expected: Optional[int] = None   # published instance count, when the set is fixed
    config: str = ""        # HuggingFace config, for repos shipping more than one
    data_files: str = ""    # glob, for repos whose layout is not a recognised split
    note: str = ""


#: Keyed by the benchmark's registered `name`. A benchmark absent from here still works —
#: it is simply reported without an expected count.
SHAPES: Dict[str, Shape] = {
    "aime24": Shape("train", 30, data_files="*.parquet"),
    "aime25": Shape("train", 30, data_files="*.jsonl"),
    "gpqa": Shape("train", 448, data_files="gpqa_diamond.csv",
                  note="Gated — needs HF_TOKEN in .env plus granted access."),
    "gsm8k": Shape("test", 1319, config="main"),
    "hle": Shape("test", 2500,
                 note="Gated — needs HF_TOKEN in .env plus granted access."),
    "deepweb": Shape("", 100),
    "leetcode": Shape("", None, note="Supplied locally; no HuggingFace source."),
    # 201 task definitions, which ship with the `programbench` pip package and are what
    # its dataset class counts. `datasets/ProgramBench-Tests` holds the per-branch test
    # blobs (~8 GB) those tasks are graded against — related, but not the same count.
    "programbench": Shape("", 201,
                          note="Task definitions from the pip package; test blobs (~8 GB) on disk."),
    "swebench_verified": Shape("test", 500),
    "swebench_pro": Shape("test", 731),
    # Listed so that looking for it finds the reason rather than nothing. Cognition
    # states they "don't currently plan to release the tasks publicly to avoid
    # contamination" and instead evaluate submitted models themselves; Epoch AI's page
    # sources its numbers from Cognition's leaderboard rather than running the set. So
    # there is no download, no harness, and no schema to write a loader against.
    "frontiercode": Shape("test", 150,
                          note="Not public — Cognition evaluates submitted models; tasks "
                               "are withheld to avoid contamination. 150 tasks (Main=100)."),
}


class Source(NamedTuple):
    """One dataset, assembled from the benchmark class plus its shape."""

    name: str
    directory: str
    hf_repo_id: str
    split: str
    expected: Optional[int]
    note: str
    config: str
    data_files: str


def sources() -> Dict[str, Source]:
    """Every benchmark that reads a dataset, with its location taken from its own class.

    A benchmark with no `path` (`exact_match` scores answers it is handed) has no dataset
    and is left out.
    """
    import agentevolver.benchmark.default  # noqa: F401  (registers the built-ins)
    from agentevolver.registry import BENCHMARK

    found: Dict[str, Source] = {}
    for class_name in BENCHMARK.module_dict:
        fields = BENCHMARK.module_dict[class_name].model_fields
        name = fields["name"].default
        path = (fields["path"].default if "path" in fields else "") or ""
        if not path:
            continue
        shape = SHAPES.get(name, Shape())
        found[name] = Source(
            name=name,
            directory=os.path.basename(path.rstrip("/")),
            hf_repo_id=(fields["hf_repo_id"].default if "hf_repo_id" in fields else "") or "",
            split=shape.split,
            expected=shape.expected,
            note=shape.note,
            config=shape.config,
            data_files=shape.data_files,
        )
    # Shapes for things that have no benchmark class yet, so the reason is still findable.
    for name, shape in SHAPES.items():
        if name not in found and shape.note:
            found[name] = Source(name, name.title(), "", shape.split, shape.expected,
                                 shape.note, shape.config, shape.data_files)
    return dict(sorted(found.items()))


SOURCES: Dict[str, Source] = sources()


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


#: Benchmarks whose registered `name` differs from their dataset class's stem in
#: `agentevolver/data/`. Everything else is found by name.
_DATASET_CLASS = {
    "swebench_verified": "SWEBenchVerifiedDataset",
    "swebench_pro": "SWEBenchProDataset",
    "gpqa": "GPQADataset",
    "gsm8k": "GSM8kDataset",
    "programbench": "ProgramBenchDataset",
    "deepweb": "DeepWebDataset",
    "leetcode": "LeetCodeDataset",
    "hle": "HLEDataset",
    "aime24": "AIME24Dataset",
    "aime25": "AIME25Dataset",
}


def _count(source: Source) -> Tuple[Optional[int], str]:
    """Instances on disk, plus why they could not be counted when they could not.

    Counting goes through the dataset class in `agentevolver/data/`, which is where this
    project puts dataset parsing — so what this reports is what a benchmark will actually
    see, not a second opinion about the same files. Counting them here independently is
    how a set with a custom layout gets reported as 2 instances when it holds 100.

    The reason is returned rather than swallowed: a bare "?" says nothing about whether a
    dataset is absent, gated, or merely laid out unexpectedly, and those need different
    responses from whoever is reading.
    """
    import agentevolver.data  # noqa: F401  (registers the dataset classes)
    from agentevolver.registry import DATASET

    class_name = _DATASET_CLASS.get(source.name)
    loader = DATASET.module_dict.get(class_name) if class_name else None
    if loader is None:
        return None, f"no dataset class registered for {source.name!r}"
    kwargs = {}
    if source.config:
        kwargs["name"] = source.config
    if source.split:
        kwargs["split"] = source.split
    try:
        return len(loader(path=os.path.join("datasets", source.directory), **kwargs)), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:70]}"


def repair(name: str, source: Source) -> bool:
    """Re-run the download over an existing directory, filling in whatever is missing.

    `ensure_dataset` skips a directory that already holds data, deliberately: fetching
    first and caching after works on a connected machine and fails on the cluster this
    runs on. The cost is that a download interrupted partway can never heal itself — the
    directory looks populated, so nothing tries again.

    That is not hypothetical. `deepweb-bench` sat with three of its five data files, and
    its dataset class raised FileNotFoundError on the two that never arrived; the
    benchmark could not initialize at all, and the directory read as present the whole
    time.

    Idempotent in result, not in bytes moved: with a `local_dir`, existing files are
    skipped only when the cache metadata beside them can vouch for them. A directory
    populated by some other means has no such metadata, so its files are fetched again
    even though they were already correct.
    """
    if not source.hf_repo_id:
        print(f"  ✘ {name}: nothing to repair from — {source.note or 'no HuggingFace source'}")
        return False

    from dotenv import load_dotenv
    from huggingface_hub import snapshot_download

    load_dotenv(os.path.join(os.path.dirname(_root()), ".env"))
    print(f"  ⟳ {name}: re-checking {source.hf_repo_id} against datasets/{source.directory}")
    try:
        snapshot_download(
            repo_id=source.hf_repo_id,
            repo_type="dataset",
            local_dir=os.path.join(_root(), source.directory),
            token=os.environ.get("HF_TOKEN") or None,
        )
    except Exception as exc:
        print(f"  ✘ {name}: {type(exc).__name__}: {str(exc).splitlines()[0][:90]}")
        return False
    return True


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
    parser.add_argument("--repair", action="store_true",
                        help="Re-check named datasets against their source, filling in "
                             "files a partial download left behind.")
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
    if args.repair:
        print("Repairing:")
        ok = all(repair(name, SOURCES[name]) for name in wanted)
    else:
        print("Fetching:")
        ok = all(fetch(name, SOURCES[name]) for name in wanted)
    print("\nDatasets on disk:")
    for name in wanted:
        report(name, SOURCES[name])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
