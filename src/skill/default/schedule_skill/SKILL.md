---
name: schedule_skill
description: Create, update, list, or run scheduled cloud agents (routines) that execute on a cron schedule, plus one-time scheduled runs. Use when the user wants a recurring cloud agent, a cron job, or a one-time scheduled run ("run this once at 3pm", "remind me to check X tomorrow"). Map the routine API/tool below to your framework's scheduler.
version: 1.0.0
type: worker
license: N/A
category: automation
requirements: [cpu]
metadata: {}
---

# Schedule Skill

Create and manage scheduled agents (routines) that run on a cron schedule or
once at a future time. These run in the cloud with zero local context, so the
prompt must be fully self-contained.

> Tooling: this assumes a routine API (create/update/list/run) and an
> environment to run in. Map to your framework's scheduler. If no environment
> exists, ask which to use (or create one) and pass its id in the job config.

## API Field Reference

**Create — required:** `name` (descriptive); exactly ONE of `cron_expression` (5-field cron in UTC, **minimum interval 1 hour**) or `run_once_at` (RFC3339 UTC timestamp, must be in the future, fires once then auto-disables); `job_config` (session configuration).

**Create — optional:** `enabled` (default true); `mcp_connections` (array of MCP servers to attach: `[{"connector_uuid","name","url"}]`).

**Update — optional (partial):** `name`, `cron_expression`, `run_once_at`, `enabled`, `job_config`, `mcp_connections` (replace), `clear_mcp_connections` (remove all).

## Cron expressions (UTC)

Cron expressions and `run_once_at` timestamps are always UTC. When the user says a local time, convert to UTC and confirm: "9am <TZ> = Xam UTC, so the cron would be `0 X * * 1-5`."

- `0 9 * * 1-5` — every weekday at 9am UTC
- `0 */2 * * *` — every 2 hours
- `0 0 * * *` — daily at midnight UTC
- `30 14 * * 1` — every Monday at 2:30pm UTC
- `0 8 1 * *` — first of every month at 8am UTC

Minimum interval is 1 hour; `*/30 * * * *` is rejected.

## Current time (for one-off runs)

**Before computing any `run_once_at` value, you MUST re-check the current time** by running `date -u +%Y-%m-%dT%H:%M:%SZ` via Bash. Do not infer the date from conversation context. Resolve relative requests ("tomorrow at 9am", "in 3 hours", "next Monday") against the fresh time, then echo BOTH the resolved local time and the UTC timestamp back for confirmation before creating the routine. If the resolved time is already in the past, ask the user to clarify rather than rolling forward.

## Workflow

### CREATE a new routine
1. **Understand the goal** — what should the cloud agent do, on which repo(s)? Remind them it runs in the cloud with no access to their local machine/files/env.
2. **Craft the prompt** — specific about what to do and what success looks like, clear about which files/areas, explicit about actions (open PRs, commit, just analyze). The prompt is the most important part — the agent starts with zero context, so it must be self-contained.
3. **Set the schedule** — ask when/how often; convert local time → UTC and confirm. For a one-time run, use `run_once_at` (re-check `date -u` first, resolve, confirm).
4. **Choose the model** — default to a balanced model (e.g. `claude-sonnet-4-6`); tell the user and let them change it.
5. **Validate connections** — infer needed services (e.g. "check Datadog and Slack me errors" → Datadog + Slack connectors). Warn about any missing and link them to connect first. Confirm the git repo(s) to clone.
6. **Review and confirm** — show the full configuration before creating; let them adjust.
7. **Create it** — call the routine API with `action: "create"`, show the result (includes the routine ID), and output a link to manage it.

### UPDATE
List routines → ask what to change → show current vs proposed → confirm → update.

### LIST
Fetch and display readably: name, schedule (human-readable), enabled/disabled, next run, repo(s).

### RUN NOW
List routines if unspecified → confirm which → execute and confirm.

## Important Notes

- These are CLOUD agents — no access to local files/services/env vars.
- Always convert cron to human-readable when displaying.
- Default to `enabled: true` unless told otherwise.
- Accept GitHub URLs in any format and normalize to full HTTPS (no `.git`).
- `ended_reason: "run_once_fired"` means a one-shot already ran; the user can re-arm by updating with a new `run_once_at`.
- If the task needs repo access (clone, open PRs, read code), remind the user the GitHub App must be installed on the repo, or the cloud agent can't access it.
