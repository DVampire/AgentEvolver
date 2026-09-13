---
name: data
description: "Owns dataset storage, downloading, inspection, repair, and adapters used by benchmarks and data workflows."
version: 1.0.0
type: module
category: data
requirements: []
metadata: {}
---
# Data

Each dataset class owns its source, default path, subsets/splits, loading and
validation. `datasets/` contains local files; data management code lives here.

| File | Responsibility |
|---|---|
| Dataset classes (`aime24.py`, `swebench.py`, etc.) | Source metadata, format-specific `_load()`, normalization, and expected sizes for known subset/split pairs. |
| `types.py` | Common `Dataset` lifecycle and `DatasetInspection` result. Calls the concrete class's methods without selecting a class by name. |
| `utils.py` | File presence and Hub snapshot transfer only; no data registry, benchmark discovery or row parsing. |
| `server.py` | `DataManager`'s `dataset_save` / `dataset_load` operations for HF records in the Hub or session storage. |

Benchmarks construct their data classes and consume the returned rows. Source/path
config defaults reference the data class and remain overridable. Evaluation, task
projection and grading stay with the benchmark. In particular, HLE loads its images
and tags in `HLEDataset`; SWE classes retain raw grading fields and the benchmark
controls which fields reach the solver.

## Class-based use

```python
from agentevolver.data import SWEBenchVerifiedDataset

# Read datasets/SWE-bench_Verified directly if populated; otherwise download
# into that directory first, then parse the local files.
dataset = SWEBenchVerifiedDataset()
print(len(dataset), dataset[0]["instance_id"])

# Optional path override follows the same local-first behavior.
dataset = SWEBenchVerifiedDataset(path="custom/swe")

check = SWEBenchVerifiedDataset.inspect()
print(check.present, check.count, check.expected, check.error)

# Explicitly reconcile a partial snapshot with its source:
SWEBenchVerifiedDataset.download(repair=True)
```

Constructors first check their default `datasets/<name>/` directory and load
existing files without a Hub request. If the directory is missing or contains
no data files, they download there and then load it. `download=False` disables
automatic acquisition; `download()` prepares files without loading records.
`inspect()` never downloads: it invokes that class's loader with downloading
disabled and reports parsing errors and counts separately from file presence.
Expected counts apply only to declared subset/split pairs, so changing a selection
does not compare it against an unrelated whole-dataset count. File presence alone
is not proof of completeness. `repair=True` rechecks the source without deleting
local files; Hub cache metadata determines which files need transferring again.
Downloads that leave only cards, metadata or empty files fail explicitly.

All paths use the project path resolver (current directory or
`AGENTEVOLVER_HOME`). Explicit paths may be absolute or project-relative. Source
and revision overrides are accepted by `download()` and constructors. Revisions
apply to transfers; an existing snapshot stays offline until explicitly repaired.
Gated sources use `HF_TOKEN` from the environment or project `.env`; `HF_ENDPOINT`
can select a mirror.

## Adding a dataset

A new dataset subclasses `Dataset`, declares its `dataset_name`, `default_path`,
source and selection defaults, and implements `_load()`. Formats with different
acquisition or inspection needs override `download()` or `inspect()` on their own
class. Register/import the class in the data package for discovery.

## Other data assets

ProgramBench defines tasks in its installed package and stores test blobs in its
local snapshot. `ProgramBenchDataset` owns both locations; counting tasks does not
prove that every test blob is complete.

`DataManager` retains its configured session `base_dir` and HF `save_to_disk`
format. `FactorMarketDataset` owns OHLCV validation, CSV/Parquet loading, panel
alignment and disjoint train/valid/test bundle creation; it uses `DataManager` for
HF/local record imports. Its domain-specific interface remains in
`factor_mining.py`. See [the factor benchmark guide](../benchmark/default/factor_mining/README.md).
