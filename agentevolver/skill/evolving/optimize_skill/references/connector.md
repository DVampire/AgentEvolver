# Improving a connector

## What it is

A connector is a directory, `{extension_root}/connector/{name}/`, holding `CONNECTOR.md`:
frontmatter declaring how to reach an MCP server and which of its actions are exposed.

**The contract**: the declared `actions` match what the live server actually offers. An action
listed but absent, or present but undocumented, is the defect this type has.

## Improving a connector

Given evaluation results, make the connector better. Edit its `CONNECTOR.md`:
- **Fix action coverage** — add missing actions the agent needed, or drop noisy ones it never uses.
- **Sharpen per-action docs** — clarify arguments and when-to-use so the agent calls them correctly; add examples for tricky ones.
- **Tune the description** for triggering (what-it-does + when-to-use, a little pushy).
- Keep it lean and explain the *why* in docs rather than piling on rigid rules.

Read the transcripts from the test runs, not just the outputs — if the agent misused an action or couldn't find the right one, that points at a doc or coverage fix. Re-register the edited connector by putting its `CONNECTOR.md` path in your `done_tool` reasoning.

---
