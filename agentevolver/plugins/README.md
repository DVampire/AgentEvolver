---
name: plugins
description: "External-provider plugins (LLMs, vector stores, search, data sources, …). A plugin is a packaging unit that surfaces on the canvas as a node (data-source plugins become datasource nodes) and returns the canonical {message, data, files} envelope. Includes the migrated Langflow bundles."
version: 2.0.0
type: module
category: plugins
requirements: []
metadata: {}
---
# Plugins

External-provider plugins adapt outside services (OpenAI, Chroma, Tavily, YouTube,
Composio, …) into AgentEvolver. Each plugin returns the canonical
`{message, data, files}` envelope, so its output composes with any other
capability. A plugin is a *packaging* unit — never a workflow step itself; it
surfaces on the canvas (dispatched through `plugin_manager`) and, for a
`data_source` kind, appears as a semantic `datasource` node.

## Structure — a plugin is a package

Every plugin lives under `default/<name>/`, shaped like a skill:

```
default/<bundle>/
├── PLUGIN.md      # manifest: frontmatter (id/kind/category/icon/source/status/requirements) + tools table
├── __init__.py    # discovery entry (auto-imported)
├── plugin.py      # registration hub
├── tools/         # one module per tool; each a BundlePlugin subclass
└── resources/icon.svg
```

Discovery is automatic (`pkgutil.iter_modules` over `default/`), so dropping a
new package here registers it with no edits elsewhere.

## Types & bases (`types.py`)

- `Plugin` — the base contract (`name`, `kind`, async `__call__` → `Response`).
- `BundlePlugin(Plugin)` — the mold for the **migrated Langflow bundles**
  (grouping metadata, preserved icon, credential resolution, canonical
  envelopes). Per-family templates build on it: `LLMPlugin`, `EmbeddingPlugin`,
  `RerankPlugin`, `VectorStorePlugin`, `MemoryPlugin`, `ComposioPlugin`.

The 86 bundles (242 tools) migrated from Langflow all subclass these. Their
provider SDKs are optional and imported lazily — a bundle registers without them
and a call returns a clear "failed result" until installed.

## Dependencies

Each `PLUGIN.md` declares its pip `requirements:`. Install per bundle with
`scripts/install-bundle.sh <bundle>` (or `--all`); the global `scripts/install.sh`
points there and does not install provider SDKs itself.
