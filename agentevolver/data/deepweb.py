import json
import os

from agentevolver.registry import DATASET
from .types import Dataset


@DATASET.register_module(force=True)
class DeepWebDataset(Dataset):
    dataset_name = 'deepweb'
    default_path = 'datasets/deepweb-bench'
    hf_repo_id = 'deepweb-bench-anon/deepweb-bench'
    default_name = None
    default_split = None
    expected_counts = {(None, None): 100}
    note = ''

    def _load(self):
        path, name, split = self.path, self.name, self.split
        local_dir = path
        cases_path = os.path.join(local_dir, "data", "cases.jsonl")

        records = []
        with open(cases_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

        # Kept as a list of raw dicts to preserve nested fields (lists/dicts) intact.
        self.data = records
