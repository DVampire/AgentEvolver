---
id: exa
name: Exa
kind: bundle
category: data
icon: lucide:ExaSearch
source: langflow/bundles/exa
status: complete
version: "1.0.0"
tools: 1
requirements: [exa-py]
---

# Exa

Migrated from the Langflow **exa** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `exa.exa_search` | Exa Search | Exa search and contents tools for agents and MCP clients. | structure |

## Icon

Uses lucide glyph `ExaSearch` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/exa/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
