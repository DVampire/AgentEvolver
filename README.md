# AgentEvolver

A self-evolving multi-agent framework. A **MetaAgent** orchestrates sub-agents to complete user tasks, while optimizer / evaluator / generator agents continuously improve the tool, skill, and agent ecosystem.

> 🌐 中文版请见 [README_zh.md](README_zh.md)

## Installation

```bash
bash scripts/install.sh
```

Creates a conda environment (`agentos`, Python 3.12), installs the package and
its dependencies, installs Node.js, and writes an `.env` template. Re-running is
safe. Add `--extras browser` for browser automation, `--uv` to use uv instead of
conda, or `--help` for all options.

Then put your API keys in `.env` at the project root:

```bash
ANTHROPIC_API_BASE='...'
ANTHROPIC_API_KEY='...'
OPENROUTER_API_BASE='...'
OPENROUTER_API_KEY='...'
```

Keys can instead be managed centrally in **Vault**, which the framework prefers
whenever it is configured and reachable, falling back to `.env` otherwise.

Full details — manual setup, Vault, and optional extras:
**➡️ [scripts/INSTALL.md](scripts/INSTALL.md)**

## Running the MetaAgent

The entry point is [`examples/run_meta_agent.py`](examples/run_meta_agent.py). It boots the MetaAgent with its sub-agents and runs a single task to completion.

```bash
conda activate agent

# 1. Run the default task
python examples/run_meta_agent.py

# 2. Run an inline task
python examples/run_meta_agent.py --task "Write a Python function to reverse a string and add unit tests."

# 3. Run a task from a task document (.html / .md under examples/tasks/)
python examples/run_meta_agent.py --task-file examples/tasks/qsar_egfr_experiment.html
```

### Options

| Flag | Description |
| --- | --- |
| `--task "<text>"` | Inline task string. Takes priority over `--task-file`. |
| `--task-file <path>` | Path to a task document (`.html` / `.md`) under `examples/tasks/`. |
| `--config <path>` | Config file (default: `configs/meta_agent.py`). |
| `--cfg-options key=value ...` | Override any config field, e.g. `--cfg-options model_name=openai/o3`. |

### What you get

- **Outputs** — each run is its own session: run artifacts, logs, and task views are written under `output/<tag>/<session-id>/` (`workspace/` for the agent's working files, `log/` for logs and rendered task views).
- On completion the log prints the final result and, if produced, the path to a memory HTML report.

Ready-made task documents live in [`examples/tasks/`](examples/tasks/) — browse them for examples of how tasks are specified.

## Interactive web frontend

`frontend/` contains a React/Vite browser UI for interactive AgentEvolver sessions. It talks to AgentEvolver through the versioned Gateway protocol; the Python runtime remains the backend.

```bash
# Terminal 1: start the Python backend
conda activate agent
agentevolver serve --transport websocket --host 127.0.0.1 --port 9876

# Terminal 2: start the browser UI
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173` (or the URL Vite prints). The Web UI connects to `ws://127.0.0.1:9876/ws` by default and lets you change the endpoint or enter a token from its **Connection** panel.

Set `AGENTEVOLVER_GATEWAY_TOKEN` before binding the Gateway outside a trusted local network. The WebSocket client reconnects automatically and asks the server to replay missed session events. See [`frontend/README.md`](frontend/README.md) for the full startup guide.

Local commands, the terminal client, and Web sessions share one project-sandbox model:
each Session gets an initially empty isolated workspace for staged inputs and generated
artifacts. Agent writes stay there rather than modifying the host checkout directly.

The Gateway requires a token when bound to a non-loopback address. Browser origins can
also be restricted with repeated `--allow-origin https://your-ui.example` options.
Restricted `bash_tool` configurations execute only inside an isolated sandbox; trusted
local host execution must be opted into explicitly with `danger_full_access`.

## Terminal commands

`agentevolver` has one CLI with three modes: run a control command directly (for
example, `agentevolver /registry`), use the terminal command loop
(`agentevolver tui`), or start the Gateway (`agentevolver serve ...`).

Generated run output is stored in the current project by default, under
`./output/<tag>/<session-id>/`. Durable project extensions live in
`./extension/`; a session stages extension changes under its own output directory
and promotes them explicitly. User-level state (overrides, caches, staging, the
deploy registry) lives in `./.agentevolver/` at the project root.
Set `AGENTEVOLVER_HOME` to place it elsewhere. Each Gateway, CLI, and TUI session
uses its own project directory with separate workspace and staged extensions; shared
service metadata and promoted extensions remain under the AgentEvolver home directory.
