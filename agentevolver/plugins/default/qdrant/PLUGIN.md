---
id: qdrant
name: Qdrant
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/qdrant
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai, langchain-qdrant]
---

# Qdrant

Migrated from the Langflow **qdrant** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `qdrant.qdrant` | Qdrant | Qdrant Vector Store with search capabilities | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/qdrant/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
