---
id: pgvector
name: PGVector
kind: bundle
category: data
icon: lucide:cpu
source: langflow/bundles/pgvector
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community, langchain-openai]
---

# PGVector

Migrated from the Langflow **pgvector** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `pgvector.pgvector` | PGVector | PGVector Vector Store with search capabilities | structure |

## Icon

Uses lucide glyph `cpu` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/pgvector/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
