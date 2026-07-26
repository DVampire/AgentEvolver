---
id: cleanlab
name: Cleanlab
kind: bundle
category: data
icon: lucide:Cleanlab
source: langflow/bundles/cleanlab
status: complete
version: "1.0.0"
tools: 3
requirements: [cleanlab-tlm]
---

# Cleanlab

Migrated from the Langflow **cleanlab** bundle. This package is in the
**structure** phase: all 3 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `cleanlab.cleanlab_evaluator` | Cleanlab Evaluator | Evaluates any LLM response using Cleanlab and outputs trust  | structure |
| `cleanlab.cleanlab_rag_evaluator` | Cleanlab RAG Evaluator | Evaluates context, query, and response from a RAG pipeline u | structure |
| `cleanlab.cleanlab_remediator` | Cleanlab Remediator |  | structure |

## Icon

Uses lucide glyph `Cleanlab` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/cleanlab/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
