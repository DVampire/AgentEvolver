---
id: scrapegraph
name: ScrapeGraph
kind: bundle
category: data
icon: lucide:ScrapeGraph
source: langflow/bundles/scrapegraph
status: complete
version: "1.0.0"
tools: 3
requirements: [scrapegraph-py]
---

# ScrapeGraph

Migrated from the Langflow **scrapegraph** bundle. This package is in the
**structure** phase: all 3 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `scrapegraph.scrapegraph_markdownify_api` | ScrapeGraph Markdownify API | Given a URL, it will return the markdownified content of the | structure |
| `scrapegraph.scrapegraph_search_api` | ScrapeGraph Search API | Given a search prompt, it will return search results using S | structure |
| `scrapegraph.scrapegraph_smart_scraper_api` | ScrapeGraph Smart Scraper API | Given a URL, it will return the structured data of the websi | structure |

## Icon

Uses lucide glyph `ScrapeGraph` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/scrapegraph/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
