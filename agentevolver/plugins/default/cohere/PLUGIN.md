---
id: cohere
name: Cohere
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/cohere
status: complete
version: "1.0.0"
tools: 3
requirements: [langchain-cohere, langchain-openai]
---

# Cohere

Migrated from the Langflow **cohere** bundle. This package is in the
**structure** phase: all 3 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `cohere.cohere_embeddings` | Cohere Embeddings | Generate embeddings using Cohere models. | structure |
| `cohere.cohere_models` | Cohere Language Models | Generate text using Cohere LLMs. | structure |
| `cohere.cohere_rerank` | Cohere Rerank | Rerank documents using the Cohere API. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/cohere/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
