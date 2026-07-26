---
id: searchapi
name: SearchAPI
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/searchapi
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community]
---

# SearchAPI

Migrated from the Langflow **searchapi** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `searchapi.search` | SearchApi | Calls the SearchApi API with result limiting. Supports Googl | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/searchapi/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
