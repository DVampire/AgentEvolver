---
id: vlmrun
name: VLM Run
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/vlmrun
status: complete
version: "1.0.0"
tools: 1
requirements: [vlmrun]
---

# VLM Run

Migrated from the Langflow **vlmrun** bundle. This package is in the
**structure** phase: all 1 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `vlmrun.vlmrun_transcription` | VLM Run Transcription | Extract structured data from audio and video using [VLM Run  | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/vlmrun/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
