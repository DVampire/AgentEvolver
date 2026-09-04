"""What counts as a dataset being on disk, and who agrees about it.

Two datasets in this repository sat in a state nobody could see: GPQA held only its
README, because the repo is gated and the download had no token, and HLE held a README
beside an empty `data/`. Any entry counted as content, so both read as already present
and were never re-downloaded — the failure surfaced much later as a load error naming a
file nobody had ever fetched, rather than as the missing download it was.
"""

import os

from agentevolver.benchmark.utils import _dir_has_content


def test_a_populated_dataset_is_present(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "test-00000.parquet").write_bytes(b"rows")
    assert _dir_has_content(str(tmp_path))


def test_a_card_without_data_is_not_a_dataset(tmp_path):
    """Exactly GPQA's state: the download was refused and left the README behind."""
    (tmp_path / "README.md").write_text("# GPQA")
    (tmp_path / ".gitattributes").write_text("*.csv filter=lfs")
    assert not _dir_has_content(str(tmp_path))


def test_an_empty_data_directory_is_not_a_dataset(tmp_path):
    """Exactly HLE's state: a card, and a data/ that never received anything."""
    (tmp_path / "README.md").write_text("# HLE")
    (tmp_path / "data").mkdir()
    assert not _dir_has_content(str(tmp_path))


def test_a_missing_directory_is_not_a_dataset(tmp_path):
    assert not _dir_has_content(str(tmp_path / "never-created"))


def _dataset_index():
    """`datasets/load.py`, loaded by path — it is a script, not an importable module."""
    import importlib.util

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "dataset_load", os.path.join(root, "datasets", "load.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_benchmark_with_data_is_in_the_dataset_index():
    """`datasets/load.py` is how someone fetches data before a run needs it, so a
    benchmark absent from it is one whose data can only appear as a surprise mid-run."""
    import agentevolver.benchmark.default  # noqa: F401  (registers the built-ins)
    from agentevolver.registry import BENCHMARK

    index = _dataset_index()
    with_data = set()
    for class_name in BENCHMARK.module_dict:
        fields = BENCHMARK.module_dict[class_name].model_fields
        if (fields["path"].default if "path" in fields else ""):
            with_data.add(fields["name"].default)

    missing = sorted(with_data - set(index.SOURCES))
    assert not missing, f"no datasets/load.py row for: {missing}"


def test_the_index_takes_each_location_from_the_benchmark_itself():
    """Where a dataset lives is the benchmark's own field, and stating it twice is how
    this index came to name three HuggingFace repos no benchmark had ever used."""
    import agentevolver.benchmark.default  # noqa: F401
    from agentevolver.registry import BENCHMARK

    index = _dataset_index()
    for class_name in BENCHMARK.module_dict:
        fields = BENCHMARK.module_dict[class_name].model_fields
        name = fields["name"].default
        row = index.SOURCES.get(name)
        if row is None:
            continue
        declared = (fields["hf_repo_id"].default if "hf_repo_id" in fields else "") or ""
        assert row.hf_repo_id == declared, (
            f"{name}: index says {row.hf_repo_id!r}, the benchmark says {declared!r}"
        )


def test_the_readme_table_lists_every_registered_benchmark():
    """The supported-benchmarks table is what someone reads to find out what this
    framework can run. A benchmark registered but absent from it is one nobody knows is
    there; a row for one that no longer registers sends them after something gone."""
    import re

    import agentevolver.benchmark.default  # noqa: F401  (registers the built-ins)
    from agentevolver.registry import BENCHMARK

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    readme = open(os.path.join(root, "agentevolver", "benchmark", "README.md")).read()
    table = readme[readme.index("## Supported benchmarks"):readme.index("### Not supported")]
    listed = set(re.findall(r"^\| `([a-z0-9_]+)` \|", table, re.M))

    registered = {
        BENCHMARK.module_dict[cls].model_fields["name"].default
        for cls in BENCHMARK.module_dict
    }
    assert not registered - listed, f"registered but not in the README table: {sorted(registered - listed)}"
    assert not listed - registered, f"in the README table but not registered: {sorted(listed - registered)}"


def test_the_readme_instance_counts_match_the_dataset_index():
    """Two places state how many instances a set holds, and a reader compares a score
    against whichever they saw. They have to agree."""
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    readme = open(os.path.join(root, "agentevolver", "benchmark", "README.md")).read()
    table = readme[readme.index("## Supported benchmarks"):readme.index("### Not supported")]

    index = _dataset_index()
    for name, count in re.findall(r"^\| `([a-z0-9_]+)` \|[^|]*\|\s*([0-9]+)\s*\|", table, re.M):
        expected = index.SOURCES[name].expected
        assert expected == int(count), (
            f"{name}: README says {count}, datasets/load.py says {expected}"
        )


def test_repair_refetches_a_directory_that_already_has_data(tmp_path, monkeypatch):
    """A download interrupted partway can never heal itself, by design.

    `ensure_dataset` skips a populated directory deliberately — fetching first and caching
    after works on a connected machine and fails on the cluster this runs on. The cost is
    that a partial snapshot reads as present forever. `deepweb-bench` sat with three of
    its five data files and its benchmark could not initialize at all, while every check
    reported the dataset as there.
    """
    index = _dataset_index()
    monkeypatch.setattr(index, "_root", lambda: str(tmp_path))
    (tmp_path / "deepweb-bench").mkdir()
    (tmp_path / "deepweb-bench" / "README.md").write_text("# card")

    called = {}

    def _snapshot(repo_id=None, repo_type=None, local_dir=None, token=None):
        called["repo_id"] = repo_id
        called["local_dir"] = local_dir
        return local_dir

    monkeypatch.setattr("huggingface_hub.snapshot_download", _snapshot)
    assert index.repair("deepweb", index.SOURCES["deepweb"])
    assert called["repo_id"] == index.SOURCES["deepweb"].hf_repo_id
    assert called["local_dir"].endswith("deepweb-bench")


def test_repair_says_so_when_there_is_no_source(monkeypatch):
    """A locally-supplied dataset cannot be repaired from anywhere; saying "done" would
    send the reader looking for a download that never happened."""
    index = _dataset_index()
    assert not index.repair("leetcode", index.SOURCES["leetcode"])
