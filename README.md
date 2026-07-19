# AgentEvolver

A self-evolving multi-agent framework. A **MetaAgent** orchestrates sub-agents to complete user tasks, while optimizer / evaluator / generator agents continuously improve the tool, skill, and agent ecosystem.

> 🌐 中文版请见 [README_zh.md](README_zh.md)

## Installation

All setup steps (Vault secret manager and the Python environment) are documented in detail here:

**➡️ [scripts/INSTALL.md](scripts/INSTALL.md)**

In short:

1. **Install & configure the secret manager (Vault)** — API keys are managed centrally in Vault instead of plaintext `.env`. See section 1 of the install guide.
2. **Set up the Python environment** (conda + pip, or uv — see the guide; requires Python 3.11+):

   ```bash
   conda create -n agent python=3.12 && conda activate agent
   pip install -e .                      # or with uv: uv sync

   # optional browser automation:
   pip install -e ".[browser]" && python -m playwright install chromium
   ```

3. **Configure `.env`** at the project root so the framework can reach Vault:

   ```bash
   VAULT_ADDR='http://127.0.0.1:8200'
   VAULT_TOKEN="<initial root token>"
   UNSEAL_TOKEN='<unseal token key1>'
   SECRET_ENGINE_PATH='cubbyhole/env'
   ```

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

- **Trace UI** — while running, the log prints `🌐 Trace UI: http://localhost:<port>`; open it to watch the agents step through the task in real time.
- **Outputs** — run artifacts, task views, and logs are written under `workspace_root/meta_agent/` (`run/` for run state, `workspace/` for the agent's working files).
- On completion the log prints the final result and, if produced, the path to a memory HTML report.

Ready-made task documents live in [`examples/tasks/`](examples/tasks/) — browse them for examples of how tasks are specified.

## Interactive web frontend

`frontend/` contains a React/Vite browser UI for interactive AgentEvolver sessions. It talks to AgentEvolver through the versioned Gateway protocol; the Python runtime remains the backend.

```bash
# Terminal 1: start the Python backend
conda activate agentos
agentevolver serve --transport websocket --host 127.0.0.1 --port 9876

# Terminal 2: start the browser UI
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173` (or the URL Vite prints). The Web UI connects to `ws://127.0.0.1:9876/ws` by default and lets you change the endpoint or enter a token from its **Connection** panel.

Set `AGENTEVOLVER_GATEWAY_TOKEN` before binding the Gateway outside a trusted local network. The WebSocket client reconnects automatically and asks the server to replay missed session events. See [`frontend/README.md`](frontend/README.md) for the full startup guide.

## Terminal commands

`agentevolver` has one CLI with three modes: run a control command directly (for
example, `agentevolver /registry`), use the terminal command loop
(`agentevolver tui`), or start the Gateway (`agentevolver serve ...`).

Generated run output is stored in the current project by default, under
`./output/<tag>/sessions/<session-id>/`. Durable project extensions live in
`./extension/`; a session stages extension changes under its own output directory
and promotes them explicitly. User-level overrides and caches use `~/.agentevolver`.
Set `AGENTEVOLVER_HOME` to place it elsewhere. Each Gateway, CLI, and TUI session
uses its own project directory with separate workspace and staged extensions; shared
service metadata and promoted extensions remain under the AgentEvolver home directory.
