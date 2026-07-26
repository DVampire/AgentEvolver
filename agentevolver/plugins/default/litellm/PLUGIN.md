---
id: litellm
name: LiteLLM
kind: bundle
category: data
icon: lucide:LiteLLM
source: langflow/bundles/litellm
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-openai]
---

# LiteLLM

Migrated from the Langflow **litellm** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `litellm.litellm_proxy` | LiteLLM Proxy | Generate text using any LLM provider via a LiteLLM proxy wit | structure |

## Icon

Uses lucide glyph `LiteLLM` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/litellm/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
