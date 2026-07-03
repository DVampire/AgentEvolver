---
name: update_config
description: Modify the agent/CLI configuration by safely editing settings files (settings.json) — permissions, hooks, env vars, model, MCP servers, plugins. Use when the user wants to change config, add a permission, set an env var, or configure an automated behavior (hook). Map the file paths/events below to your framework's actual config system.
version: 1.0.0
type: worker
---

# Update Config Skill

Modify configuration by updating settings files. Adapt the concrete file paths
(`~/.claude/settings.json`, `.claude/settings.json`) and hook events below to
your framework's equivalent config locations and event hooks.

## When Hooks Are Required (Not Memory)

If the user wants something to happen automatically in response to an EVENT, they need a **hook** configured in settings, not a memory/preference. Memory cannot trigger automated actions.

**These require hooks:**
- "Before compacting, ask me what to preserve" → PreCompact hook
- "After writing files, run prettier" → PostToolUse hook with a `Write|Edit` matcher
- "When I run bash commands, log them" → PreToolUse hook with a `Bash` matcher
- "Always run tests after code changes" → PostToolUse hook

**Hook events:** PreToolUse, PostToolUse, PreCompact, PostCompact, Stop, Notification, SessionStart.

## CRITICAL: Read Before Write

**Always read the existing settings file before making changes.** Merge new settings with existing ones — never replace the entire file.

## CRITICAL: Use a clarifying question for ambiguity

When the request is ambiguous, ask before editing:
- Which settings file to modify (user / project / local)
- Whether to add to existing arrays or replace them
- Specific values when multiple options exist

## Decision: simple-settings command vs direct edit

**Prefer a settings command/UI** for simple scalar settings: `theme`, `editorMode`, `verbose`, `model`, `language`, `permissions.defaultMode`.

**Edit settings.json directly** for: hooks, complex permission rules (allow/deny arrays), environment variables, MCP server config, plugin config.

## Workflow

1. **Clarify intent** — ask if the request is ambiguous.
2. **Read existing file** — Read the target settings file.
3. **Merge carefully** — preserve existing settings, especially arrays.
4. **Edit file** — use Edit (if the file doesn't exist, ask the user to create it first).
5. **Confirm** — tell the user exactly what changed.

## Merging Arrays (Important!)

When adding to permission/hook arrays, **merge with existing**, don't replace.

**WRONG** (replaces existing permissions):
```json
{ "permissions": { "allow": ["Bash(npm *)"] } }
```

**RIGHT** (preserves existing + adds new):
```json
{
  "permissions": {
    "allow": [
      "Bash(git *)",      // existing
      "Edit(.claude)",    // existing
      "Bash(npm *)"       // new
    ]
  }
}
```

## Hook structure (example)

A hook is `{event: [{matcher, hooks: [{type, command}]}]}`. Example — format code after a write:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "command",
        "command": "jq -r '.tool_response.filePath // .tool_input.file_path' | { read -r f; prettier --write \"$f\"; } 2>/dev/null || true"
      }]
    }]
  }
}
```

Hook types: `command` (run a shell command, receives event JSON on stdin), `prompt`, or `agent`. A hook's JSON stdout can return fields like `systemMessage` to surface a message.

## Common Mistakes to Avoid

1. Replacing instead of merging — always preserve existing settings.
2. Wrong file — ask if scope is unclear.
3. Invalid JSON — validate syntax after changes.
4. Forgetting to read first — always read before write.

## Troubleshooting Hooks

1. Check the settings file (user vs project).
2. Verify JSON syntax — invalid JSON silently fails.
3. Check the matcher — does it match the tool name? (`Bash`, `Write`, `Edit`).
4. Check the hook type — `command`, `prompt`, or `agent`?
5. Test the command — run it manually.
6. Run with debug logging to see hook execution.
