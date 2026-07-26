---
id: aiml
name: AI/ML API
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/aiml
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-openai]
---

# AI/ML API

Migrated from the Langflow **aiml** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `aiml.aiml` | AI/ML API | Generates text using AI/ML API LLMs. | structure |
| `aiml.aiml_embeddings` | AI/ML API Embeddings | Generate embeddings using the AI/ML API. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/aiml/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
