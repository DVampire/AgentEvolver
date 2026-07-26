---
id: git
name: Git
kind: bundle
category: data
icon: resources/icon.svg
source: langflow/bundles/git
status: complete
version: "1.0.0"
tools: 2
requirements: [langchain-community]
---

# Git

Migrated from the Langflow **git** bundle. This package is in the
**structure** phase: all 2 tools are registered as
`BundleTool` stubs and are being implemented one by one.

## Tools

| id | name | description | status |
|----|------|-------------|--------|
| `git.git` | Git |  | structure |
| `git.gitextractor` | GitExtractor | Analyzes a Git repository and returns file contents and comp | structure |

## Icon

Preserved verbatim from Langflow at `resources/icon.svg`.

## Provenance

- Langflow bundle: `src/bundles/lfx-bundles/src/lfx_bundles/git/`
- Migration mold: `agentevolver/plugins/bundle.py` (`BundleTool`)
