# Generic run dashboard

A read-only view of any AgentEvolver run, independent of its task, agent class,
benchmark, or deployment profile. It never calls a model or drives execution.

`examples/run_meta_agent.py` starts it by default through the deployment manager.
`examples/run_website_evolution_demo.py` uses that same launcher; no ECHO-specific
dashboard or runtime hooks are needed. All public pages share gateway port **9876**:
open `/sites/` to choose a run monitor, benchmark monitor, or deployed website.
`--monitor-port 8766` only chooses a preferred **internal** port, not a second port
to forward. Use `--no-monitor` to disable the monitor. The public URL printed by
Deploy is authoritative.

## Sources of truth

| View | Source |
| --- | --- |
| Launcher status | PID plus process start identity; explicit terminal status |
| Agent lifecycle, parents, subscriptions | `kernel.snapshot()` published every 5 seconds |
| Last action, model calls, tokens, recorded cost | Incrementally consumed trace JSONL |
| Deployed websites | Deploy registry, restricted to sites used by this run or sourced in its workspace |
| Extension inventory | Configured extension manifest, not an inferred count of successful evolutions |
| Request inspector | Existing final model-request HTML; never reconstructed model input |

The eight capability families are Agent, Tool, Skill, Workflow, Connector, Plugin,
Environment, and Memory. Agent prompts belong to their agents. A registered
candidate is **not** evidence that the run generated it, adopted it, or used it
successfully. Likewise, a completed turn does not mean a resident agent has exited.
Unknown lifecycle facts remain unknown; there is no invented completion percentage.

The state file is `P.LOG_RUN_MONITOR` (`<session log>/run_monitor.json`). The reader
retains bounded event summaries and per-agent usage, not raw messages. Partial
JSONL lines wait for completion; file truncation and replacement reset aggregates.
Cost coverage is displayed because some providers do not report a price. Cache
ratio uses cache reads divided by uncached input + cache reads + cache writes.

## Attach without restarting an agent

Activate `agentos`, then run:

```bash
python -m agentevolver.visual.run.server attach \
  --log-root /absolute/session/log \
  --session-id SESSION_ID --pid LAUNCHER_PID \
  --title 'Agent run' --port 8766 \
  --extension-root /absolute/extension
```

This deploys a monitor only. It cannot inject runtime publishing into an existing
Python process: the page labels itself **trace only**, and idle subscribers may
not appear until they emit trace events. A stopped legacy launcher with no explicit
terminal status is shown as interrupted, not automatically declared successful.

## Access and lifecycle

The gateway binds **127.0.0.1:9876**; forward that one port through SSH or VS Code.
Deployment links and monitor links open under `/s/<site>/` on the same gateway.
The gateway has no public authentication, so do not expose its port publicly.
Model-request pages can contain private task content.

Only named static assets, `/api/status`, and scoped model-request HTML are served.
No workspace directory listing, arbitrary file endpoint, shell, pause/stop control,
or URL proxy is exposed. Request pages use a sandboxed CSP; the dashboard uses DOM
text nodes for trace-derived strings rather than interpreting them as HTML.

Deploy runs in a separate short-lived bootstrap process, so the read-only service
survives agent teardown and remains available for reviewing the final state. Its
site ID is `run-monitor-<state-path hash>` in the normal Deploy registry. Stop that
site with the deployment manager when it is no longer needed. Starting this view
does not stop, restart, or mutate the observed agents.

The palette and base layout come from `visual/benchmark/style.css`; only run-view
layout additions live here. All visible interface labels are English.
