---
id: zep
name: Zep
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/zep
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community]
---

# Zep

Migrated from the Langflow **zep** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `zep.zep` | Zep Chat Memory | Retrieves and store chat messages from Zep. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/zep/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
