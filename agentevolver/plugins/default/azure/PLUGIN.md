---
id: azure
name: Azure
kind: bundle
category: data
icon: lucide:Azure
source: langflow/bundles/azure
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-openai]
---

# Azure

Migrated from the Langflow **azure** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `azure.azure_openai` | Azure OpenAI | Generate text using Azure OpenAI LLMs. | structure |
| `azure.azure_openai_embeddings` | Azure OpenAI Embeddings | Generate embeddings using Azure OpenAI models. | structure |

## Icon

Uses lucide glyph `Azure` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/azure/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
