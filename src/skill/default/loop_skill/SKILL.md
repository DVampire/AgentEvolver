---
name: loop_skill
description: Run a prompt or slash command on a recurring interval — or with no interval, let the model self-pace based on the task. Use when the user wants a recurring task, to poll for status, or to run something repeatedly (e.g. "check the deploy every 5 minutes", "keep running X"). Do NOT use for one-off tasks. Map the scheduling/wakeup tools below to your framework's equivalents.
version: 1.0.0
type: worker
license: N/A
category: automation
requirements: [cpu]
metadata: {}
---

# Loop Skill

Parse the input into `[interval] <prompt…>` and either schedule it on a fixed
interval or, with no interval, self-pace it dynamically.

> Tooling: this skill assumes a recurring scheduler (cron-style), a self-wakeup
> tool (schedule the next turn after a delay), and a background monitor (wake on
> an event). Map these to your framework's equivalents.

## Parsing (in priority order)

1. **Leading token**: if the first whitespace-delimited token matches `^\d+[smhd]$` (e.g. `5m`, `2h`), that's the interval; the rest is the prompt.
2. **Trailing "every" clause**: otherwise, if the input ends with `every <N><unit>` or `every <N> <unit-word>` (e.g. `every 20m`, `every 5 minutes`), extract that as the interval and strip it from the prompt. Only match when what follows "every" is a time expression — `check every PR` has no interval.
3. **No interval**: otherwise the entire input is the prompt and you self-pace dynamically (see Dynamic mode).

If the resulting prompt is empty, show usage `/loop [interval] <prompt>` and stop.

Examples:
- `5m /babysit-prs` → interval `5m`, prompt `/babysit-prs` (rule 1)
- `check the deploy every 20m` → interval `20m`, prompt `check the deploy` (rule 2)
- `run tests every 5 minutes` → interval `5m`, prompt `run tests` (rule 2)
- `check the deploy` → no interval → dynamic mode (rule 3)
- `check every PR` → no interval → dynamic mode (rule 3 — "every" not followed by time)
- `5m` → empty prompt → show usage

## Fixed-interval mode (rules 1 & 2)

Supported suffixes: `s` (rounded up to nearest minute, min 1), `m`, `h`, `d`. Convert to cron:

| Interval pattern    | Cron expression          | Notes                                    |
|---------------------|--------------------------|------------------------------------------|
| `Nm` where N ≤ 59   | `*/N * * * *`            | every N minutes                          |
| `Nm` where N ≥ 60   | `0 */H * * *`            | round to hours (H = N/60, must divide 24)|
| `Nh` where N ≤ 23   | `0 */N * * *`            | every N hours                            |
| `Nd`                | `0 0 */N * *`            | every N days at midnight local           |
| `Ns`                | treat as `ceil(N/60)m`   | cron minimum granularity is 1 minute     |

If the interval doesn't cleanly divide its unit (e.g. `7m`, `90m`), pick the nearest clean interval and tell the user what you rounded to before scheduling.

Then:
1. Schedule the recurring job with: `cron` (the expression above), `prompt` (the parsed prompt verbatim, slash commands passed through), `recurring: true`.
2. Briefly confirm: what's scheduled, the cron expression, the human-readable cadence, that recurring tasks auto-expire after the retention window, and how to cancel (include the job ID).
3. **Then immediately execute the parsed prompt now** — don't wait for the first fire. If it names a skill, invoke it as a skill action (`{type:"skill", name:...}`); otherwise act on it directly.

## Dynamic mode (rule 3 — no interval)

The user wants you to self-pace. Decide what makes the next iteration worth running — a passage of time, or an observable event.

1. **Run the parsed prompt now.**
2. **If the next run is gated on an event** (CI finishing, a log line, a file change, a PR comment) and no monitor is already running for it: arm a persistent background monitor now. Its events wake the loop immediately — you don't wait for the delay. Arm once; on later iterations check first and skip if one is already running.
3. **Briefly confirm**: that you're self-pacing, whether a monitor is the primary wake signal, that you ran the task now, and the fallback delay you're about to pick. Write this as text *before* scheduling the wakeup — the turn ends as soon as that returns.
4. **As the last action, schedule the next wakeup** with:
   - `delaySeconds`: with a monitor armed this is the **fallback heartbeat** (lean 1200–1800s; frequent idle ticks are pure overhead). Without a monitor it's the cadence — pick based on what you observed.
   - `reason`: one short sentence on why you picked that delay.
   - `prompt`: the full original `/loop` input verbatim, prefixed with `/loop ` so the next firing re-enters this skill and continues the loop.
5. **If woken by an event** rather than the prompt: handle the event in the loop's context, then reschedule with the same prompt and the same fallback delay.
6. **To stop the loop**: omit the wakeup call and cancel any monitor you armed.
