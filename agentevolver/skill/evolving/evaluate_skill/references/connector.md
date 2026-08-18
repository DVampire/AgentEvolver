# Evaluating a connector

## What it is

A connector is a directory, `{extension_root}/connector/{name}/`, holding `CONNECTOR.md`:
frontmatter declaring how to reach an MCP server and which of its actions are exposed.

**The contract**: the declared `actions` match what the live server actually offers. An action
listed but absent, or present but undocumented, is the defect this type has.

## Evaluating a connector

Goal: measure whether the connector's actions actually let an agent accomplish tasks — empirically, not just by reading the docs.

### Static check (always)

Read the `CONNECTOR.md` and score it: are the required frontmatter fields present (`connection`, `actions`); is each action documented with purpose + arguments; does the description state what-it-does and when-to-use. Use `inspect_connector_tool` to confirm the connector is registered and to get its connection, actions, and directory. Validate structure with:
```bash
python {skill_dir}/scripts/connector/validate_connector.py <connector_dir>
```

### Connection check

Confirm the server is reachable and exposes the declared actions — connect with `scripts/connector/connections.py` and compare the live tool list against the `actions` in the frontmatter. Flag missing or undocumented actions.

### Empirical check (with-connector vs baseline)

The heart of quantitative evaluation is: do the connector's actions help versus not having them? There is no separate eval tool — **MetaAgent runs the comparison by dispatching agents** (see Orchestration). For each realistic test task:
- **with-connector run**: dispatch `general_agent` with the connector available.
- **baseline run**: dispatch `general_agent` without it.

Compare task success. See `references/connector/evaluation.md` for the evaluation methodology (writing good task questions, judging tool use). Produce a scored report: static + connection + empirical, with concrete improvement suggestions.

---
