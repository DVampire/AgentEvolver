---
id: deepseek
name: DeepSeek
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/deepseek
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai]
---

# DeepSeek

Migrated from the Langflow **deepseek** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `deepseek.deepseek` | DeepSeek | Generate text using DeepSeek LLMs. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/deepseek/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
