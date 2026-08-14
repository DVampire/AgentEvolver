---
name: spill_default
description: "Provides the local spill store, which writes oversized tool output to owner-only files under the machine-level runtime root."
version: 1.0.0
type: collection
category: infrastructure
requirements: []
metadata: {}
---
# Built-in spill stores

Provides the local spill store, which writes oversized tool output to owner-only files
under the machine-level runtime root.

| Store | Locator it returns |
|---|---|
| `LocalSpillStore` | An absolute filesystem path under `output/.runtime/spill` |

`LocalSpillStore` is the default: `spill_manager` instantiates it on first use when no
other store has been registered, so spilling works with no configuration.

Its retrieval hint names `read_file_tool`, `grep_search_tool`, and `bash_tool`, because
for this store the locator really is a readable path. A store whose locator is a URI or
an object key owns a different hint — which is why the hint travels with the reference
rather than being assembled by the consumer.
