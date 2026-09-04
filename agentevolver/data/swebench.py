from agentevolver.registry import DATASET
from agentevolver.utils import assemble_workspace_path


class _SWEBenchDataset:
    """Rows of a SWE-bench split, read from a local snapshot directory.

    Every other benchmark's data parsing lives in this module; these two were the
    exception, loading through `load_dataset` inside their launchers and so landing in the
    HuggingFace cache rather than under `datasets/`. Sitting here instead means one place
    answers "what is in this dataset" for every benchmark in the project.

    Rows are kept as raw dicts. They carry the oracle — `patch`, `test_patch`, and for Pro
    the `fail_to_pass` / `pass_to_pass` lists — because grading happens on the host where
    the answer key already lives. Deciding which fields may reach an agent's container is
    the benchmark class's job (`safe_fields`), not this one's, and the two must not both
    have an opinion about it.
    """

    #: Set by subclasses; the split loaded when a caller names none.
    default_split = "test"

    def __init__(self, path, name=None, split=None):
        """
        Args:
            path: Local dataset directory (the HF snapshot under `datasets/`).
            name: Unused; kept for signature compatibility with the other datasets.
            split: Dataset split; defaults to the only split these publish.
        """
        from datasets import load_dataset

        self.path = path
        self.name = name
        self.split = split or self.default_split

        local_dir = assemble_workspace_path(path)
        self.data = list(load_dataset(local_dir, split=self.split))

    def __len__(self):
        return len(self.data)

    def __getitem__(self, index):
        return self.data[index]


@DATASET.register_module(force=True)
class SWEBenchVerifiedDataset(_SWEBenchDataset):
    """SWE-bench Verified — 500 human-validated Python issues from 12 repositories.

    Columns include `instance_id`, `repo`, `base_commit`, `problem_statement`, and the
    grading material: `patch`, `test_patch`, `eval_script`, `log_parser`, `eval_type`,
    plus the `image` the official harness runs the instance in.
    """


@DATASET.register_module(force=True)
class SWEBenchProDataset(_SWEBenchDataset):
    """SWE-bench Pro — 731 issues across Python, Go, JS and TS, with longer patches.

    Adds `requirements` and `interface` to the issue text, and states the hidden suite as
    explicit `fail_to_pass` / `pass_to_pass` name lists rather than an eval script.
    """
