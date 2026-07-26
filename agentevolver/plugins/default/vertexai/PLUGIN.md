---
id: vertexai
name: Vertex AI
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/vertexai
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-google-vertexai, langchain-openai]
---

# Vertex AI

Migrated from the Langflow **vertexai** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `vertexai.vertexai` | Vertex AI | Generate text using Vertex AI LLMs. | structure |
| `vertexai.vertexai_embeddings` | Vertex AI Embeddings | Generate embeddings using Google Cloud Vertex AI models. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/vertexai/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
