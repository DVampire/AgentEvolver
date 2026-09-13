import os
import json
import pandas as pd

from agentevolver.registry import DATASET
from .types import Dataset


@DATASET.register_module(force=True)
class LeetCodeDataset(Dataset):
    dataset_name = 'leetcode'
    default_path = 'datasets/leetcode'
    hf_repo_id = ''
    default_name = None
    default_split = 'test'
    expected_counts = {}
    note = 'Supplied locally; no Hub source.'

    def __init__(self, path=None, name=None, split=None, lang="python3", **kwargs):
        self.lang = lang
        super().__init__(path, name, split, **kwargs)

    def _load(self):
        path, name, split = self.path, self.name, self.split
        # 1. Path normalization
        path = path
        
        # 2. Load metadata file
        # Expected path structure: /data/leetcode/test/metadata.jsonl
        metadata_file = os.path.join(path, split, "metadata.jsonl")
        
        if not os.path.exists(metadata_file):
            raise FileNotFoundError(f"Metadata file not found: {metadata_file}")
        
        data_rows = []
        
        with open(metadata_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # --- LeetCode specific loading logic ---

                # 1. Read problem description (Markdown content)
                # row['file'] example: "./question/1.two_sum.md"
                # Parse to absolute path, usually relative to metadata.jsonl directory
                rel_file_path = row.get("file", "")
                question_content = ""
                
                metadata_dir = os.path.dirname(os.path.abspath(metadata_file))

                if rel_file_path:
                    raw_path = os.path.join(metadata_dir, rel_file_path)
                    abs_file_path = os.path.normpath(raw_path)
                    if os.path.exists(abs_file_path):
                        with open(abs_file_path, "r", encoding="utf-8") as qf:
                            question_content = qf.read()
                    else:
                        print(f"[Warning] 路径不存在: {abs_file_path}")
                        continue
                    
                code_template = row.get("code_template", {})
            

                # 4. Construct data row
                data_row = {
                    "task_id": str(row.get("id", "")), # to string
                    "name": row.get("name", ""),
                    "question": question_content,
                    
                    # LeetCode datasets are typically for Code Generation,
                    # real verification requires Sandbox execution,
                    # true_answer is usually empty or contains test cases (if any)
                    "true_answer": "", 
                    "code_template": code_template,
                    "lang": self.lang,
                    "task": "LeetCode",
                    "file_name": rel_file_path
                }
                
                data_rows.append(data_row)
        
        # 4. Convert to DataFrame
        self.data = pd.DataFrame(data_rows)
