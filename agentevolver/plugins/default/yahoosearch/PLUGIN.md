---
id: yahoosearch
name: Yahoo Search
kind: bundle
category: data
icon: lucide:trending-up
source: langflow/bundles/yahoosearch
status: complete
version: "1.0.0"
tools: 1
requirements: [yfinance]
---

# Yahoo Search

Migrated from the Langflow **yahoosearch** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `yahoosearch.yahoo` | Yahoo! Finance |  | structure |

## Icon

Uses lucide glyph `trending-up` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/yahoosearch/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
