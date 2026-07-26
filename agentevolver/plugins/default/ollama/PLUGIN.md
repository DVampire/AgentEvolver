---
id: ollama
name: Ollama
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/ollama
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-ollama, langchain-openai]
---

# Ollama

Migrated from the Langflow **ollama** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `ollama.ollama` | Ollama | Generate text using Ollama Local LLMs. | structure |
| `ollama.ollama_embeddings` | Ollama Embeddings | Generate embeddings using Ollama models. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/ollama/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
