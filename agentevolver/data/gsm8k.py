import pandas as pd

from agentevolver.registry import DATASET
from .types import Dataset


@DATASET.register_module(force=True)
class GSM8kDataset(Dataset):
    dataset_name = 'gsm8k'
    default_path = 'datasets/gsm8k'
    hf_repo_id = 'openai/gsm8k'
    default_name = 'main'
    default_split = 'test'
    expected_counts = {('main', 'test'): 1319}
    note = ''

    def _load(self):
        path, name, split = self.path, self.name, self.split
        from datasets import load_dataset, get_dataset_config_names

        local_dir = path
        all_subsets = ["main", "socratic"]
        if name == "all":
            target_subsets = all_subsets
        elif name in all_subsets:
            target_subsets = [name]
        else:
            print(f"[Warning] Unknown subset '{name}'. Defaulting to 'main'.")
            target_subsets = ["main"]

        try:
            available = get_dataset_config_names(local_dir)
        except Exception:
            available = all_subsets
        target_subsets = [s for s in target_subsets if not available or s in available] or [target_subsets[0]]

        data_rows = []
        for subset_name in target_subsets:
            ds = load_dataset(local_dir, name=subset_name)
            split_name = split if split in ds else list(ds.keys())[0]
            for i, row in enumerate(ds[split_name]):
                raw_answer = str(row.get("answer", ""))
                if "####" in raw_answer:
                    parts = raw_answer.split("####")
                    reasoning_content = parts[0].strip()
                    final_answer = parts[-1].strip()
                else:
                    reasoning_content = ""
                    final_answer = raw_answer.strip()

                data_rows.append({
                    "task_id": f"{subset_name}_{i + 1}",
                    "question": str(row.get("question", "")),
                    "true_answer": final_answer.replace(",", ""),
                    "reasoning": reasoning_content,
                    "task": "GSM8k",
                    "subset": subset_name,
                    "file_name": "",
                })

        self.data = pd.DataFrame(data_rows)



    def get_task_description(self):
        return "You will answer a mathemetical reasoning question. Think step by step. The last line of your response should be of the following format: 'Answer: $VALUE' where VALUE is a numerical value."
