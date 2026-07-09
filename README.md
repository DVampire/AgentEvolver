# AgentEvolver

A self-evolving multi-agent framework. A **MetaAgent** orchestrates sub-agents to complete user tasks, while optimizer / evaluator / generator agents continuously improve the tool, skill, and agent ecosystem.

> 🌐 中文版请见 [README_zh.md](README_zh.md)

## Installation

All setup steps (Vault secret manager, opencode, and the Python environment) are documented in detail here:

**➡️ [scripts/INSTALL.md](scripts/INSTALL.md)**

In short:

1. **Install & configure the secret manager (Vault)** — API keys are managed centrally in Vault instead of plaintext `.env`. See section 1 of the install guide.
2. **Install opencode** — see section 2.
3. **Set up the Python environment**:

   ```bash
   conda create -n agent python=3.12
   conda activate agent
   pip install -r scripts/requirements.txt

   # Browser automation
   pip install playwright && playwright install
   pip install browser-use && browser-use install
   ```

4. **Configure `.env`** at the project root so the framework can reach Vault:

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
- **Outputs** — run artifacts, task views, and logs are written under `work_dir/meta_agent/` (`run/` for run state, `workspace/` for the agent's working files).
- On completion the log prints the final result and, if produced, the path to a memory HTML report.

Ready-made task documents live in [`examples/tasks/`](examples/tasks/) — browse them for examples of how tasks are specified.
