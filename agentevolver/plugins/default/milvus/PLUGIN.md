---
id: milvus
name: Milvus
kind: bundle
category: data
icon: lucide:Milvus
source: langflow/bundles/milvus
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-milvus, langchain-openai]
---

# Milvus

Migrated from the Langflow **milvus** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `milvus.milvus` | Milvus | Milvus vector store with search capabilities | structure |

## Icon

Uses lucide glyph `Milvus` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/milvus/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
