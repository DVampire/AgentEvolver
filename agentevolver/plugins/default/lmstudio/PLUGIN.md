---
id: lmstudio
name: LM Studio
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/lmstudio
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-openai]
---

# LM Studio

Migrated from the Langflow **lmstudio** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `lmstudio.lmstudioembeddings` | LM Studio Embeddings | Generate embeddings using LM Studio. | structure |
| `lmstudio.lmstudiomodel` | LM Studio | Generate text using LM Studio Local LLMs. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/lmstudio/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
