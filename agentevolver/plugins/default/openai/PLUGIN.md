---
id: openai
name: Openai
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/openai
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-openai]
---

# Openai

Migrated from the Langflow **openai** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `openai.openai` | OpenAI Embeddings | Generate embeddings using OpenAI models. | structure |
| `openai.openai_chat_model` | OpenAI | Generates text using OpenAI LLMs. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/openai/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
