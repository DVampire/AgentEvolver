---
id: chroma
name: Chroma
kind: bundle
category: data
icon: lucide:Chroma
source: langflow/bundles/chroma
status: complete
version: "1.0.0"
tools: 2
requirements: [chromadb, langchain-chroma, langchain-openai]
---

# Chroma

Migrated from the Langflow **chroma** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `chroma.chroma` | Chroma DB | Chroma Vector Store with search capabilities | structure |
| `chroma.local_db` | Local DB | Local Vector Store with search capabilities | structure |

## Icon

Uses lucide glyph `Chroma` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/chroma/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
