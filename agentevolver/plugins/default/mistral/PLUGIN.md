---
id: mistral
name: Mistral
kind: bundle
category: data
icon: lucide:MistralAI
source: langflow/bundles/mistral
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-mistralai, langchain-openai]
---

# Mistral

Migrated from the Langflow **mistral** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `mistral.mistral` | MistralAI | Generates text using MistralAI LLMs. | structure |
| `mistral.mistral_embeddings` | MistralAI Embeddings | Generate embeddings using MistralAI models. | structure |

## Icon

Uses lucide glyph `MistralAI` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/mistral/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
