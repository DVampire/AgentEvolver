---
id: confluence
name: Confluence
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/confluence
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community]
---

# Confluence

Migrated from the Langflow **confluence** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `confluence.confluence` | Confluence | Confluence wiki collaboration platform | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/confluence/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
