---
name: keybindings_help
description: Create or modify the keybindings.json file to customize keyboard shortcuts. Use when the user wants to rebind a key, add a chord shortcut, change the submit key, or customize keybindings. Map the file path to your framework's actual keybindings config.
version: 1.0.0
type: worker
---

# Keybindings Skill

Create or modify `~/.claude/keybindings.json` (adapt to your framework's keybindings file) to customize keyboard shortcuts.

## CRITICAL: Read Before Write

**Always read the keybindings file first** (it may not exist yet). Merge changes with existing bindings — never replace the entire file.

- Use **Edit** for modifications to existing files.
- Use **Write** only if the file does not exist yet.

## File Format

A keybindings file has a `$schema`, a `$docs` link, and a `bindings` array of
`{context, bindings: {<keystroke>: <action>}}` objects. Always include the
`$schema` and `$docs` fields.

## Keystroke Syntax

**Modifiers** (combine with `+`):
- `ctrl` (alias: `control`)
- `alt` (aliases: `opt`, `option`) — note: `alt` and `meta` are identical in terminals
- `shift`
- `meta` (aliases: `cmd`, `command`)

**Special keys:** `escape`/`esc`, `enter`/`return`, `tab`, `space`, `backspace`, `delete`, `up`, `down`, `left`, `right`.

**Chords:** space-separated keystrokes, e.g. `ctrl+k ctrl+s` (1-second timeout between keystrokes).

**Examples:** `ctrl+shift+p`, `alt+enter`, `ctrl+k ctrl+n`.

## Unbinding Default Shortcuts

Set a key to `null` to remove its default binding.

## How User Bindings Interact with Defaults

- User bindings are **additive** — appended after the default bindings.
- To **move** a binding: unbind the old key (`null`) AND add the new binding.
- A context only needs to appear in the user's file if they want to change something in it.

## Common Patterns

- **Rebind a key**: e.g. change the external-editor shortcut from `ctrl+g` to `ctrl+e` — bind `ctrl+e` to the action and unbind `ctrl+g` with `null`.
- **Add a chord binding**: bind a space-separated chord like `ctrl+k ctrl+t` to an action.

## Behavioral Rules

1. Only include contexts the user wants to change (minimal overrides).
2. Validate that actions and contexts are from the known lists.
3. Warn proactively if a chosen key conflicts with reserved shortcuts or common tools like tmux (`ctrl+b`) and screen (`ctrl+a`).
4. A new binding for an existing action is additive (the default still works unless explicitly unbound).
5. To fully replace a default binding, unbind the old key AND add the new one.

## Validation

After editing, validate: every context appears once, `bindings` is the correct
shape (wrapper object → array), no context-name typos, no key defined twice in
one context (JSON keeps only the last), and no conflicts with terminal/OS
reserved shortcuts.
