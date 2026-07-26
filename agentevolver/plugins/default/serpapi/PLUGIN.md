---
id: serpapi
name: SerpAPI
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/serpapi
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community]
---

# SerpAPI

Migrated from the Langflow **serpapi** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `serpapi.serp` | Serp Search API | Call Serp Search API with result limiting | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/serpapi/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
