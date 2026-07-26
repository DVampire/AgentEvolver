---
id: wikipedia
name: Wikipedia
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/wikipedia
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-community]
---

# Wikipedia

Migrated from the Langflow **wikipedia** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `wikipedia.wikidata` | Wikidata | Performs a search using the Wikidata API. | structure |
| `wikipedia.wikipedia` | Wikipedia | Call Wikipedia API. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/wikipedia/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
