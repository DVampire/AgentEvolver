---
id: codeagents
name: Code Agents
kind: bundle
category: data
icon: lucide:bot
source: langflow/bundles/codeagents
status: complete
version: "1.0.0"
tools: 2
requirements: []
---

# Code Agents

Migrated from the Langflow **codeagents** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `codeagents.codeact_agent_smolagents` | CodeAct Agent (Smolagents) | A code-based agent using smolagents CodeAgent for complex ta | structure |
| `codeagents.open_ds_star_agent` | OpenDsStar Agent | A tool-based DS-Star agent using LangGraph for complex data  | structure |

## Icon

Uses lucide glyph `bot` (no custom SVG in Langflow).

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/codeagents/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
