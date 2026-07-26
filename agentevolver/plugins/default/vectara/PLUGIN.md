---
id: vectara
name: Vectara
kind: bundle
category: data
icon: lucide:Vectara
source: langflow/bundles/vectara
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-community, langchain-openai]
---

# Vectara

Migrated from the Langflow **vectara** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `vectara.vectara` | Vectara | Vectara Vector Store with search capabilities | structure |
| `vectara.vectara_rag` | Vectara RAG | Vectara | structure |

## Icon

Uses lucide glyph `Vectara` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/vectara/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
