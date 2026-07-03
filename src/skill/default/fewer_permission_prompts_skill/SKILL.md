---
name: fewer_permission_prompts_skill
description: Scan transcripts for common read-only Bash and MCP tool calls, then add a prioritized allowlist to the project settings to reduce permission prompts. Use when the user wants fewer permission prompts or wants to build a permission allowlist from their actual usage. Map file paths to your framework's transcript and settings locations.
version: 1.0.0
type: worker
license: N/A
category: config
requirements: [cpu]
metadata: {}
---

# Fewer Permission Prompts Skill

Look through the transcripts' MCP and bash tool calls, and based on those, make a prioritized list of patterns to add to the permission allowlist to reduce permission prompts. **Focus on read-only commands.**

The permission format is: `Bash(foo*)`, `Bash(foo)`, `Bash(foo bar *)`, `mcp__slack__slack_read_thread`, etc. Add these to the project `.claude/settings.json` under `permissions.allow` (adapt paths to your framework).

## Steps

1. **Locate transcripts.** Session transcripts live at `~/.claude/projects/<sanitized-cwd>/*.jsonl`. Each line is a JSON object; tool calls appear as `assistant` messages with `message.content[]` entries of `type: "tool_use"`. The `name` field identifies the tool (e.g. `"Bash"`, `"mcp__slack__slack_read_thread"`); for Bash, `input.command` is the shell string. Scan recent transcripts across the user's projects dir (cap at ~50 most-recently-modified files so it stays fast).

2. **Extract tool-call frequencies.**
   - Bash: parse `input.command`, take the leading command token (handling `sudo`, `timeout`, pipes, `&&`, env-var prefixes). Record the command + first subcommand (e.g. `git status`, `gh pr view`, `ls`, `cat`).
   - MCP: record the full tool name (e.g. `mcp__slack__slack_read_thread`).
   - Count occurrences.

3. **Filter to read-only.** Keep only commands that don't mutate state (`ls`, `cat`, `git status/log/diff/show`, `rg`, `grep`, `find`, `gh pr/issue/run view|list|diff`, `gh api` GET, `docker ps/logs`, `kubectl get/describe`, read/get/list/search/view MCP tools…). Drop anything that writes, deletes, renames, pushes, merges, installs, or runs a build/test with side effects. When in doubt, leave it out.

   **Never allowlist a pattern that grants arbitrary code execution** — a wildcard for any of these is equivalent to arbitrary execution:
   - Interpreters: `python`/`python3`, `node`, `bun`, `deno`, `ruby`, `perl`, `php`, `lua`.
   - Shells: `bash`, `sh`, `zsh`, `fish`, `eval`, `exec`, `ssh`.
   - Package runners: `npx`, `bunx`, `uvx`, `uv run`.
   - Task-runner wildcards: `npm run *`, `yarn run *`, `pnpm run *`, `bun run *`, `make *`, `just *`, `cargo run *`, `go run *` — an exact `Bash(bun run typecheck)` is fine, `Bash(bun run *)` is not.
   - `gh api *`, `docker run`/`exec`, `kubectl exec`, `sudo`.

4. **Drop commands the harness already auto-allows** — they never prompt, so don't add them. (Many read-only basics like `cat`, `ls`, `head`, `git status`, `gh pr view`, `rg`, `jq`, `diff` are typically auto-allowed; check your framework's read-only command list rather than re-allowlisting them.)

5. **Pick the narrowest pattern** that covers the observed usage. Many variants → `Bash(git log *)` (note the space before `*`, required for prefix matching). A single common exact invocation → `Bash(foo)` with no wildcard. MCP → full tool name verbatim. Never widen to the point of granting arbitrary execution or mutation.

6. **Prioritize.** Rank by count descending. Drop anything seen fewer than ~3 times. Cap at the top ~20.

7. **Present the prioritized list** as a markdown table — rank, pattern, count, one-line description:

   | # | Pattern | Count | Notes |
   |---|---------|-------|-------|
   | 1 | `Bash(git status *)` | 142 | repo status checks |
   | 2 | `Bash(gh pr view *)` | 87 | PR inspection |
   | 3 | `mcp__slack__slack_read_thread` | 54 | Slack thread reads |

8. **Merge into the project `.claude/settings.json`** (not the user file, not the local file). Create if missing. Preserve existing keys and existing `permissions.allow` entries; de-duplicate; don't remove or reorder unrelated fields.

9. **Report back.** What you added (count + examples), what was already allowed, and what you skipped and why (e.g. "dropped `rm`/`git push` — not read-only; dropped `cat`/`ls` — already auto-allowed").

Do not add anything to `permissions.deny` or `permissions.ask`. Do not touch any other settings field.
