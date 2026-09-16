"""Dataset classes own source selection, loading and local inspection."""

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from agentevolver.data import Dataset, DeepWebDataset, HLEDataset, LeetCodeDataset, SWEBenchVerifiedDataset
from agentevolver.data.utils import has_dataset_files


class LocalRows(Dataset):
    dataset_name = "sample"
    default_path = "datasets/sample"
    hf_repo_id = "test/sample"
    expected_counts = {(None, "test"): 2}

    def _load(self):
        self.data = json.loads((Path(self.path) / "rows.json").read_text())


@pytest.mark.parametrize("filename,content,present", [
    ("data/test.parquet", b"rows", True), ("README.md", b"card", False),
    (".cache/rows.json", b"metadata", False), ("rows.parquet", b"", False),
])
def test_file_presence_excludes_metadata_and_empty_files(tmp_path, filename, content, present):
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    assert has_dataset_files(str(tmp_path)) is present


def test_missing_directory_and_empty_subdirectory_are_not_data(tmp_path):
    assert not has_dataset_files(str(tmp_path / "absent"))
    (tmp_path / "data").mkdir()
    assert not has_dataset_files(str(tmp_path))


def test_benchmark_defaults_come_from_their_data_classes():
    from agentevolver.benchmark import benchmark_manager
    from agentevolver.registry import DATASET

    catalog = {cls.dataset_name: cls for cls in DATASET.module_dict.values()
               if issubclass(cls, Dataset) and cls.dataset_name}
    for benchmark in benchmark_manager.catalog():
        if not benchmark.config.get("path"):
            continue
        cls = catalog[benchmark.name]
        assert benchmark.config["path"] == cls.default_path
        assert (benchmark.config.get("hf_repo_id") or "") == cls.hf_repo_id


def test_download_load_and_inspection_share_the_class_path(tmp_path, monkeypatch):
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(project))
    monkeypatch.chdir(elsewhere)
    calls = []

    def snapshot(**kwargs):
        calls.append(kwargs)
        (Path(kwargs["local_dir"]) / "rows.json").write_text("[1, 2]")

    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot)
    dataset = LocalRows(revision="v1")
    assert dataset.path == str(project / "datasets/sample")
    assert dataset.data == [1, 2]
    check = LocalRows.inspect()
    assert check.path == dataset.path and check.count == check.expected == 2
    assert calls[0]["repo_id"] == LocalRows.hf_repo_id and calls[0]["revision"] == "v1"
    assert len(calls) == 1 and not (elsewhere / "datasets").exists()
    LocalRows()
    assert len(calls) == 1  # Populated snapshots stay offline.


def test_download_and_repair_honor_path_and_source_overrides(tmp_path, monkeypatch):
    directory = tmp_path / "custom/location"
    directory.mkdir(parents=True)
    (directory / "rows.json").write_text("[1]")
    called = []

    def snapshot(**kwargs):
        called.append(kwargs)
        (Path(kwargs["local_dir"]) / "missing.json").write_text("[2]")

    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot)
    assert LocalRows.download(str(directory)) == str(directory)
    assert not called
    LocalRows.download(str(directory), hf_repo_id="test/override", repair=True)
    assert called[0]["local_dir"] == str(directory) and called[0]["repo_id"] == "test/override"
    assert (directory / "rows.json").read_text() == "[1]"
    assert (directory / "missing.json").exists()
    assert LocalRows.inspect(str(directory)).count == 1


def test_inspection_cannot_download_even_when_missing_or_malformed(tmp_path, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("inspection attempted a download")

    monkeypatch.setattr("huggingface_hub.snapshot_download", forbidden)
    assert not LocalRows.inspect(str(tmp_path)).present
    with pytest.raises(FileNotFoundError):
        LocalRows(path=str(tmp_path), download=False)
    (tmp_path / "rows.json").write_text("broken")
    check = LocalRows.inspect(str(tmp_path))
    assert check.present and check.count is None and "JSONDecodeError" in check.error


def test_download_with_only_metadata_is_not_reported_as_success(tmp_path, monkeypatch):
    def snapshot(**kwargs):
        (Path(kwargs["local_dir"]) / "README.md").write_text("Dataset card")

    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot)
    with pytest.raises(FileNotFoundError, match="left no dataset files"):
        LocalRows.download(str(tmp_path))


def test_local_only_dataset_cannot_be_repaired_from_an_unspecified_source(tmp_path):
    with pytest.raises(FileNotFoundError, match="no `hf_repo_id`"):
        LeetCodeDataset.download(str(tmp_path), repair=True)


def test_deepweb_owns_jsonl_parsing_and_inspection(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "cases.jsonl").write_text('{"case_id":"1","dimensions":["reasoning"]}\n')
    dataset = DeepWebDataset(path=str(tmp_path))
    assert dataset[0]["dimensions"] == ["reasoning"]
    assert DeepWebDataset.inspect(str(tmp_path)).count == 1


def test_leetcode_owns_its_split_layout(tmp_path):
    split = tmp_path / "test"
    split.mkdir()
    (split / "question.md").write_text("Find the pair.")
    (split / "metadata.jsonl").write_text('{"id":"1","file":"question.md"}\n')
    dataset = LeetCodeDataset(path=str(tmp_path), lang="cpp")
    assert dataset[0]["question"] == "Find the pair." and dataset[0]["lang"] == "cpp"
    assert LeetCodeDataset.inspect(str(tmp_path)).count == 1


def test_hle_preserves_raw_images_and_tags_for_the_evaluator(tmp_path, monkeypatch):
    from PIL import Image
    picture = Image.new("RGB", (2, 2))
    row = {"id": "1", "question": "Test", "answer": "42", "image": picture}
    data = tmp_path / "data"
    data.mkdir()
    (data / "tags.json").write_text('{"0001":["math"]}')
    monkeypatch.setattr("datasets.load_dataset", lambda *args, **kwargs: [row])
    dataset = HLEDataset(path=str(tmp_path))
    assert dataset.records[0]["image"] is picture
    assert dataset[0]["image"] is picture and dataset[0]["true_answer"] == "42"
    assert dataset.tags == {"0001": ["math"]}


def test_swe_class_reads_single_parquet_and_retains_grading_fields(tmp_path):
    path = tmp_path / "test.parquet"
    pd.DataFrame([{"instance_id": "1", "problem_statement": "Fix it", "patch": "SECRET"}]).to_parquet(path)
    dataset = SWEBenchVerifiedDataset(path=str(path), download=True, hf_repo_id="")
    assert dataset[0]["patch"] == "SECRET"
    assert SWEBenchVerifiedDataset.inspect(str(path)).count == 1


@pytest.mark.parametrize("already_downloaded", [True, False])
def test_swe_default_constructor_reuses_or_populates_datasets_directory(tmp_path, monkeypatch, already_downloaded):
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    directory = tmp_path / "datasets/SWE-bench_Verified"
    calls = []

    def write_data():
        directory.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"instance_id": "local-1", "problem_statement": "Fix it"}]).to_parquet(
            directory / "test.parquet")

    if already_downloaded:
        write_data()

    def snapshot(**kwargs):
        calls.append(kwargs)
        assert not already_downloaded, "existing datasets must load without downloading"
        assert kwargs["local_dir"] == str(directory)
        assert kwargs["repo_id"] == SWEBenchVerifiedDataset.hf_repo_id
        write_data()

    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot)
    dataset = SWEBenchVerifiedDataset()
    assert dataset.path == str(directory) and dataset[0]["instance_id"] == "local-1"
    assert len(calls) == (0 if already_downloaded else 1)


def test_inspection_expected_counts_follow_the_selected_subset(tmp_path, monkeypatch):
    monkeypatch.setattr(LocalRows, "expected_counts", {(None, "test"): 2, ("other", "test"): 4})
    (tmp_path / "rows.json").write_text("[1, 2]")
    assert LocalRows.inspect(str(tmp_path)).expected == 2
    check = LocalRows.inspect(str(tmp_path), name="other")
    assert check.expected == 4 and check.count == 2


@pytest.mark.asyncio
async def test_benchmark_downloads_and_reads_its_configured_path(tmp_path, monkeypatch):
    from agentevolver.benchmark import BenchmarkManager
    monkeypatch.setenv("AGENTEVOLVER_HOME", str(tmp_path))
    called = []

    def snapshot(**kwargs):
        directory = Path(kwargs["local_dir"])
        called.append(directory)
        pd.DataFrame([{"ID": "1", "Problem": "Test question", "Answer": "42"}]).to_parquet(directory / "train.parquet")

    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot)
    manager = BenchmarkManager()
    await manager.configure("aime24", path="custom/aime", hf_repo_id="test/aime", base_dir=str(tmp_path / "run"))
    try:
        task = await manager.reset("aime24")
        assert task.ground_truth == "42"
        assert called == [tmp_path / "custom/aime"]
        assert not (tmp_path / "datasets/aime").exists()
    finally:
        await manager.cleanup()


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
