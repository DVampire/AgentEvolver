---
id: firecrawl
name: Firecrawl
kind: bundle
category: data
icon: lucide:puzzle
source: langflow/bundles/firecrawl
status: complete
version: "1.0.0"
tools: 4
requirements: []
---

# Firecrawl

Migrated from the Langflow **firecrawl** bundle. This package is in the
**structure** phase: all 4 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `firecrawl.firecrawl_crawl_api` | Firecrawl Crawl API | Crawls a URL and returns the results. | structure |
| `firecrawl.firecrawl_map_api` | Firecrawl Map API | Maps a URL and returns the results. | structure |
| `firecrawl.firecrawl_scrape_api` | Firecrawl Scrape API | Scrapes a URL and returns the results. | structure |
| `firecrawl.firecrawl_search_api` | Firecrawl Search API | Searches the web and returns the results. | structure |

## Icon

Uses lucide glyph `puzzle` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/firecrawl/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
