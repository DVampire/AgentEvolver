from agentevolver.registry import DATASET
from .types import Dataset


@DATASET.register_module(force=True)
class ProgramBenchDataset(Dataset):
    dataset_name = 'programbench'
    default_path = 'datasets/ProgramBench-Tests'
    hf_repo_id = 'programbench/ProgramBench-Tests'
    default_name = None
    default_split = None
    expected_counts = {(None, None): 201}
    note = 'Task definitions from the programbench package; test blobs (~8 GB) on disk.'

    def _load(self):
        from programbench.utils.load_data import load_all_instances
        instances = load_all_instances(include_tests=True)

        self.data = instances
        self.instances = {i["instance_id"]: i for i in instances}
