---
id: spider
name: Spider
kind: bundle
category: data
icon: lucide:puzzle
source: langflow/bundles/spider
status: complete
version: "1.0.0"
tools: 1
requirements: [spider-client]
---

# Spider

Migrated from the Langflow **spider** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `spider.spider` | Spider Web Crawler & Scraper | Spider API for web crawling and scraping. | structure |

## Icon

Uses lucide glyph `puzzle` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/spider/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
