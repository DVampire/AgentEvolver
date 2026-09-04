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


def test_every_benchmark_has_a_row_in_the_dataset_index():
    """`datasets/load.py` is how someone fetches data before a run needs it, so a
    benchmark absent from it is one whose data can only appear as a surprise mid-run."""
    import importlib.util

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "dataset_load", os.path.join(root, "datasets", "load.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import agentevolver.benchmark.default  # noqa: F401  (registers the built-ins)
    from agentevolver.registry import BENCHMARK

    # exact_match and leetcode carry no downloadable dataset of their own.
    named = {
        BENCHMARK.module_dict[cls].model_fields["name"].default
        for cls in BENCHMARK.module_dict
    } - {"exact_match", "leet_code_benchmark", "leetcode"}
    missing = sorted(named - set(module.SOURCES))
    assert not missing, f"no datasets/load.py row for: {missing}"
