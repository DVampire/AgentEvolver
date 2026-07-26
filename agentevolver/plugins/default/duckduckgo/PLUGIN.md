---
id: duckduckgo
name: Duckduckgo
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/duckduckgo
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community]
---

# Duckduckgo

Migrated from the Langflow **duckduckgo** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `duckduckgo.duck_duck_go_search_run` | DuckDuckGo Search | Search the web using DuckDuckGo with customizable result lim | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/duckduckgo/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
