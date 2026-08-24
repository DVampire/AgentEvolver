---
name: benchmark
description: "Provides versioned benchmark definitions and the manager used to load and execute them. Built-in benchmark adapters live in `default/`; dataset parsing belongs to `data/`."
version: 1.0.0
type: module
category: benchmark
requirements: []
metadata: {}
---
# Benchmark

Provides versioned benchmark definitions and the manager used to load and execute them.
Built-in benchmark adapters live in `default/`; dataset parsing belongs to `data/`.

| File | Responsibility |
|---|---|
| `types.py` | Benchmark and configuration contracts |
| `context.py` | Benchmark registry and lifecycle state |
| `server.py` | Public `benchmark_manager` facade |
| `utils.py` | Shared benchmark helpers |

New benchmarks should implement the base contract and register through the normal registry
instead of adding selection logic to the server.

## Data comes from `datasets/` first

Every benchmark stores its data under `datasets/<name>/`. Declare an `hf_repo_id` field and
call `ensure_dataset(<name>, self.hf_repo_id)` (in `utils.py`) from `initialize()` before
loading: a missing or empty `datasets/<name>/` is snapshot-downloaded from HuggingFace, and
otherwise the local copy is used untouched. Both `hf_repo_id` and `path` stay
config-overridable, and `HF_ENDPOINT` selects a mirror.

Downloading first and caching after would work on a connected machine and fail on the
cluster this runs on, which is why the order is fixed rather than a preference.
