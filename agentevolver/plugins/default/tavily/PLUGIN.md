---
id: tavily
name: Tavily
kind: bundle
category: data
icon: lucide:TavilyIcon
source: langflow/bundles/tavily
status: complete
version: "1.0.0"
tools: 2
requirements: []
---

# Tavily

Migrated from the Langflow **tavily** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `tavily.tavily_extract` | Tavily Extract API |  | structure |
| `tavily.tavily_search` | Tavily Search API |  | structure |

## Icon

Uses lucide glyph `TavilyIcon` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/tavily/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
