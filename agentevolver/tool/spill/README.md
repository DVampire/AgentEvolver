---
name: spill
description: "Archives a complete tool result and returns a retrieval locator."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# Spill

Archives a complete tool result and returns a retrieval locator. Tool dispatch preserves the inline result, too.

| Path | Responsibility |
|---|---|
| `types.py` | The `SpillStore` contract, plus `SpillSource` and `SpillRef` |
| `__init__.py` | `save_text` / `use_store` — the active store, and the failure it absorbs |
| `default/local.py` | `LocalSpillStore` — owner-only files under `output/.runtime/spill` |

## Why it exists

Large command results need a durable archive for audit and retrieval without rerunning
the command. Archiving does not authorize clipping the inline result. Request pressure
is handled by explicit conversation compaction, or a clear overflow failure when the
complete input cannot fit. Prefer appropriately scoped queries before executing them.

## The contract

`save_text` writes the content **whole** and returns a `SpillRef` — an opaque
locator, the exact character count, and a sentence telling the agent how to read it
back. It raises on a genuine storage failure.

`save_text` is the caller-facing wrapper and **never raises**: it
logs and returns `None`. That asymmetry is deliberate. The caller is already
holding a result the tool produced successfully, and a full disk is a reason to
lose the transcript, not a reason to report the command as failed. The tool
pipeline keeps its complete inline result when the save returns `None`.

The store owns storage only — not retention, not the decision about when a result
is too big, and not the rewriting of the result. Those live with the caller, which
is why `save_text` never inspects the size it is handed.

## The locator is opaque

The local store renders a filesystem path; another store may render a URI or a key.
Consumers print the locator next to `retrieval_hint` and never parse it, so
swapping in a remote store does not require touching a single tool.

## Storage safety

Artifacts land in `<output/.runtime/spill>/session-<sha256(session)[:16]>/<random>-<name>`.

- The session is **hashed**, so an id containing a slash cannot climb out of the root.
- The suggested name is reduced to one path segment (`Path(...).name`, then a
  character filter), so it is a naming hint and never a path.
- Directories are `0700`, files `0600`, and each file is opened with `O_EXCL` — a
  symlink planted at the target makes the write fail instead of redirecting it.
