---
id: bing
name: Bing
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/bing
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community]
---

# Bing

Migrated from the Langflow **bing** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `bing.bing_search_api` | Bing Search API | Call the Bing Search API. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/bing/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
