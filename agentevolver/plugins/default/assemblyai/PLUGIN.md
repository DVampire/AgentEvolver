---
id: assemblyai
name: AssemblyAI
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/assemblyai
status: complete
version: "1.0.0"
tools: 5
requirements: [assemblyai]
---

# AssemblyAI

Migrated from the Langflow **assemblyai** bundle. This package is in the
**structure** phase: all 5 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `assemblyai.assemblyai_get_subtitles` | AssemblyAI Get Subtitles | Export your transcript in SRT or VTT format for subtitles an | structure |
| `assemblyai.assemblyai_lemur` | AssemblyAI LeMUR | Apply Large Language Models to spoken data using the Assembl | structure |
| `assemblyai.assemblyai_list_transcripts` | AssemblyAI List Transcripts | Retrieve a list of transcripts from AssemblyAI with filterin | structure |
| `assemblyai.assemblyai_poll_transcript` | AssemblyAI Poll Transcript | Poll for the status of a transcription job using AssemblyAI | structure |
| `assemblyai.assemblyai_start_transcript` | AssemblyAI Start Transcript | Create a transcription job for an audio file using AssemblyA | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/assemblyai/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
