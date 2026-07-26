---
id: faiss
name: Faiss
kind: bundle
category: data
icon: lucide:FAISS
source: langflow/bundles/faiss
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community, langchain-openai]
---

# Faiss

Migrated from the Langflow **faiss** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `faiss.faiss` | FAISS | FAISS Vector Store with search capabilities | structure |

## Icon

Uses lucide glyph `FAISS` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/faiss/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
