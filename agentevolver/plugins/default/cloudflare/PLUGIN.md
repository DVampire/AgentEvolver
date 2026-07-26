---
id: cloudflare
name: Cloudflare
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/cloudflare
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community, langchain-openai]
---

# Cloudflare

Migrated from the Langflow **cloudflare** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `cloudflare.cloudflare` | Cloudflare Workers AI Embeddings | Generate embeddings using Cloudflare Workers AI models. | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/cloudflare/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
