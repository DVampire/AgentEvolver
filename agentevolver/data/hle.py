import json
from pathlib import Path

import pandas as pd

from agentevolver.registry import DATASET
from .types import Dataset


@DATASET.register_module(force=True)
class HLEDataset(Dataset):
    """HLE rows and category tags, including images decoded by the HF adapter."""

    dataset_name = "hle"
    default_path = "datasets/hle"
    hf_repo_id = "cais/hle"
    default_split = "test"
    expected_counts = {(None, "test"): 2500}
    note = "Gated: requires HF_TOKEN and granted access."

    def _load(self):
        from datasets import load_dataset

        # The evaluator consumes raw rows so question IDs, images and answer types
        # retain their source representation. Data consumers also get normalized rows.
        self.records = list(load_dataset(self.path, split=self.split))
        tags_path = Path(self.path) / "data" / "tags.json"
        self.tags = json.loads(tags_path.read_text()) if tags_path.exists() else {}
        data_rows = []
        for row in self.records:
            question = str(row.get("question", "")).strip()
            if not question:
                continue
            data_rows.append({
                "task_id": str(row.get("id", "")),
                "question": question,
                "true_answer": str(row.get("answer", "")),
                "answer_type": str(row.get("answer_type", "exactMatch")),
                "image": row.get("image"),
                "category": str(row.get("category", "")),
                "raw_subject": str(row.get("raw_subject", "")),
                "task": "HLE",
            })
        self.data = pd.DataFrame(data_rows)
