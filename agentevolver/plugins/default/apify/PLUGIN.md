---
id: apify
name: Apify
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/apify
status: complete
version: "1.0.0"
tools: 1
requirements: [apify-client]
---

# Apify

Migrated from the Langflow **apify** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `apify.apify_actor` | Apify Actors |  | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/apify/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
