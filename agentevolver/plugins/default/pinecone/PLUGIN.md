---
id: pinecone
name: Pinecone
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/pinecone
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai, langchain-pinecone]
---

# Pinecone

Migrated from the Langflow **pinecone** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `pinecone.pinecone` | Pinecone | Pinecone Vector Store with search capabilities | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/pinecone/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
