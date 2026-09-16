import pandas as pd

from agentevolver.registry import DATASET
from .types import Dataset


@DATASET.register_module(force=True)
class AIME25Dataset(Dataset):
    dataset_name = 'aime25'
    default_path = 'datasets/AIME25'
    hf_repo_id = 'opencompass/AIME2025'
    default_name = 'all'
    default_split = 'test'
    expected_counts = {('all', 'test'): 30}
    note = ''

    def _load(self):
        path, name, split = self.path, self.name, self.split
        from datasets import load_dataset, get_dataset_config_names

        local_dir = path
        try:
            all_configs = get_dataset_config_names(local_dir)
        except Exception:
            all_configs = []

        if name and name != "all" and name in all_configs:
            target_configs = [name]
        elif all_configs:
            target_configs = all_configs
        else:
            target_configs = [None]

        data_rows = []
        for config in target_configs:
            ds = load_dataset(local_dir, name=config) if config else load_dataset(local_dir)
            split_name = split if split in ds else list(ds.keys())[0]
            for i, row in enumerate(ds[split_name]):
                q_text = str(row.get("question", "")).strip()
                if not q_text:
                    continue
                raw_id = row.get("ID") or row.get("id") or f"{config or 'aime25'}_{i + 1}"
                data_rows.append({
                    "task_id": str(raw_id),
                    "question": q_text,
                    "true_answer": str(row.get("answer", "")).strip(),
                    "task": "AIME 2025",
                    "subset": config or "",
                    "file_name": "",
                })

        self.data = pd.DataFrame(data_rows)
