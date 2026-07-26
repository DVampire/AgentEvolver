---
id: homeassistant
name: Home Assistant
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/homeassistant
status: complete
version: "1.0.0"
tools: 2
requirements: []
---

# Home Assistant

Migrated from the Langflow **homeassistant** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `homeassistant.home_assistant_control` | Home Assistant Control |  | structure |
| `homeassistant.list_home_assistant_states` | List Home Assistant States |  | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/homeassistant/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
