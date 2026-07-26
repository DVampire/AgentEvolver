---
id: baidu
name: Baidu
kind: bundle
category: data
icon: lucide:BaiduQianfan
source: langflow/bundles/baidu
status: complete
version: "1.0.0"
tools: 1
requirements: [langchain-community]
---

# Baidu

Migrated from the Langflow **baidu** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `baidu.baidu_qianfan_chat` | Qianfan | Generate text using Baidu Qianfan LLMs. | structure |

## Icon

Uses lucide glyph `BaiduQianfan` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/baidu/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
