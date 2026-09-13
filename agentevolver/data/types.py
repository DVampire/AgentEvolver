"""Common lifecycle for dataset classes; formats and source defaults stay on each class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from agentevolver.utils import assemble_workspace_path
from .utils import download_snapshot, has_dataset_files


@dataclass(frozen=True)
class DatasetInspection:
    path: str
    present: bool
    count: Optional[int] = None
    expected: Optional[int] = None
    error: str = ""


class Dataset(ABC):
    """A dataset owns its source, loading method and inspection defaults.

    Construction reuses local files, or downloads missing data to the class's
    default directory before loading. Set ``download=False`` for strictly local
    reads, or call ``download()`` to prepare files without loading records.
    ``inspect()`` always loads locally and reports parsing errors without fetching.
    """

    dataset_name = ""
    default_path = ""
    hf_repo_id = ""
    default_name = None
    default_split = "test"
    expected_counts = {}
    note = ""

    def __init__(self, path=None, name=None, split=None, *, hf_repo_id=None,
                 download=True, repair=False, revision=None):
        self.path = self.resolve_path(path)
        self.name = self.default_name if name is None else name
        self.split = self.default_split if split is None else split
        if download or repair:
            self.path = type(self).download(self.path, hf_repo_id=hf_repo_id,
                                            revision=revision, repair=repair)
        self._load()

    @classmethod
    def resolve_path(cls, path=None):
        return assemble_workspace_path(cls.default_path if path is None else path)

    @classmethod
    def download(cls, path=None, *, hf_repo_id=None, revision=None, repair=False):
        """Fetch this dataset's snapshot; existing local files stay usable offline."""
        return download_snapshot(
            cls.resolve_path(path), cls.hf_repo_id if hf_repo_id is None else hf_repo_id,
            revision=revision, repair=repair,
        )

    @classmethod
    def is_present(cls, path=None):
        """File presence only; subclasses can specialize their storage layout."""
        return has_dataset_files(cls.resolve_path(path))

    @classmethod
    def inspect(cls, path=None, *, name=None, split=None):
        """Check this class's local representation and selected split."""
        path = cls.resolve_path(path)
        name = cls.default_name if name is None else name
        split = cls.default_split if split is None else split
        expected = cls.expected_counts.get((name, split))
        if not cls.is_present(path):
            return DatasetInspection(path, False, expected=expected, error="No dataset files found.")
        try:
            dataset = cls(path=path, name=name, split=split, download=False)
            return DatasetInspection(path, True, count=len(dataset), expected=expected)
        except Exception as exc:
            return DatasetInspection(path, True, expected=expected, error=f"{type(exc).__name__}: {exc}")

    @abstractmethod
    def _load(self):
        """Read and validate this dataset's format into ``self.data``."""

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        return self.data.iloc[index] if hasattr(self.data, "iloc") else self.data[index]
