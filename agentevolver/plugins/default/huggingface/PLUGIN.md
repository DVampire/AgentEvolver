---
id: huggingface
name: Hugging Face
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/huggingface
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-community, langchain-huggingface, langchain-openai]
---

# Hugging Face

Migrated from the Langflow **huggingface** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `huggingface.huggingface` | Hugging Face | Generate text using Hugging Face Inference APIs. | structure |
| `huggingface.huggingface_inference_api` | Hugging Face Embeddings Inference | Generate embeddings using Hugging Face Text Embeddings Infer | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/huggingface/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
