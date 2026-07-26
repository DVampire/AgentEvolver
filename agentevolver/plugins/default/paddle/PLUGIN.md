---
id: paddle
name: Paddle
kind: bundle
category: data
icon: lucide:file-search
source: langflow/bundles/paddle
status: complete
version: "1.0.0"
tools: 1
requirements: [paddleocr]
---

# Paddle

Migrated from the Langflow **paddle** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `paddle.paddleocr` | PaddleOCR | Use PaddleOCR for either layout-aware document parsing into  | structure |

## Icon

Uses lucide glyph `file-search` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/paddle/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
