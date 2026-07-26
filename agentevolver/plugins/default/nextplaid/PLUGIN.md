---
id: nextplaid
name: NextPlaid
kind: bundle
category: data
icon: lucide:NextPlaid
source: langflow/bundles/nextplaid
status: complete
version: "1.0.0"
tools: 2
requirements: []
---

# NextPlaid

Migrated from the Langflow **nextplaid** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `nextplaid.nextplaid` | NextPlaid |  | structure |
| `nextplaid.vllm_multivector_embeddings` | vLLM Multivector Embeddings |  | structure |

## Icon

Uses lucide glyph `NextPlaid` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/nextplaid/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
