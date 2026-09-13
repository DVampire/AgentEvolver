import pandas as pd

from agentevolver.registry import DATASET
from .types import Dataset


@DATASET.register_module(force=True)
class AIME24Dataset(Dataset):
    dataset_name = 'aime24'
    default_path = 'datasets/AIME24'
    hf_repo_id = 'Maxwell-Jia/AIME_2024'
    default_name = None
    default_split = 'train'
    expected_counts = {(None, 'train'): 30}
    note = ''

    def _load(self):
        path, name, split = self.path, self.name, self.split
        from datasets import load_dataset

        local_dir = path
        ds = load_dataset(local_dir)

        # AIME_2024 ships a single split ("train"); use the requested split if present.
        split_name = split if split in ds else list(ds.keys())[0]
        records = ds[split_name]

        data_rows = []
        for row in records:
            q_text = str(row.get("Problem", "")).strip()
            if not q_text:
                continue
            answer = row.get("Answer", "")
            if isinstance(answer, (int, float)):
                answer = str(int(answer))
            data_rows.append({
                "task_id": str(row.get("ID", "")),
                "question": q_text,
                "true_answer": str(answer).strip(),
                "reasoning": str(row.get("Solution", "")).strip(),
                "task": "AIME 2024",
                "file_name": "",
            })

        self.data = pd.DataFrame(data_rows)
