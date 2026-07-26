---
id: perplexity
name: Perplexity
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/perplexity
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai]
---

# Perplexity

Migrated from the Langflow **perplexity** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `perplexity.perplexity` | Perplexity | Generate text using Perplexity LLMs. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/perplexity/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
