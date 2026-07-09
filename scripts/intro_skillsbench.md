# SkillsBench Evaluation Guide

This document explains how to run end-to-end evaluation on **SkillsBench (~87 Dockerized tasks)**. It covers: the evaluation pipeline, environment and key setup, the first run, and watching progress and results.

SkillsBench is a multi-turn interactive benchmark: each task runs in its own Docker container, the agent issues shell commands and receives stdout/stderr feedback, and the in-container `tests/test.sh` finally evaluates and gives a reward (0.0–1.0).

> Evaluation pipeline: YAML config → Harbor CLI → `AgentEvolver` agent → in-container AgentEvolver bus → `tests/test.sh` computes reward
>
> Task data + Harbor framework live in a separate repo: https://github.com/DVampire/AgentEvolver_SkillsBench (see its README for more on tuning / inspecting results)
>
> Main repo: https://github.com/DVampire/AgentEvolver

> 🌐 中文版请见 [intro_skillsbench_zh.md](intro_skillsbench_zh.md)

---

## **TL;DR**

Assuming you have already configured Vault, opencode, and the Python environment per [INSTALL.md](INSTALL.md):

```bash
# 0) Clone tasks + Harbor framework into datasets/
cd datasets && git clone git@github.com:DVampire/AgentEvolver_SkillsBench.git && cd ..

# 1) Confirm Docker Compose V2 (must be v2.x)
docker compose version

# 2) Install Harbor + dependencies
uv pip install --system -e datasets/AgentEvolver_SkillsBench/harbor
pip install -r scripts/requirements.txt pyyaml python-dotenv

# 3) Run the full set
python examples/run_skillsbench_harbor.py --config configs/main-with-skills-agentos.yaml
#    results are written to workdir/skillsbench/<job_name>/
```

---

## **Evaluation scope & artifacts**

- **Task count**: ~87 tasks, each in its own Docker container.
- **Dataset**: default `tasks` (with skills metadata); use `tasks-no-skills` to omit skills.
- **Per-task workflow**: the full AgentEvolver agent pipeline (in-container bus), multi-turn shell interaction until the task ends or the step budget is exceeded.
- **Evaluation**: after the task ends, the in-container `tests/test.sh` gives a reward (0.0–1.0).
- **Artifacts**: each run produces per-task records and a summary under `workdir/skillsbench/<job_name>/`.

---

## **Environment setup**

Set up the base Vault / opencode / Python environment per [INSTALL.md](INSTALL.md) first. Below are the SkillsBench-specific extra steps.

### **Step 1: Clone the data repo into `datasets/`**

```bash
cd datasets && git clone git@github.com:DVampire/AgentEvolver_SkillsBench.git && cd ..
```

- Task data: `datasets/SkillsAgentEvolver_SkillsBenchBench/tasks` (use `tasks-no-skills` for the no-skills version)
- Harbor framework: `datasets/AgentEvolver_SkillsBench/harbor`

### **Step 2: Docker + Compose V2**

```bash
docker compose version    # must be v2.x, otherwise Harbor reports "unknown shorthand flag: 'p' in -p"
```

If the Compose V2 plugin is missing, install it manually:

```bash
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -L "https://github.com/docker/compose/releases/download/v2.29.2/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

### **Step 3: Install Harbor + dependencies**

```bash
uv pip install --system -e datasets/AgentEvolver_SkillsBench/harbor   # no uv: curl -LsSf https://astral.sh/uv/install.sh | sh
pip install -r scripts/requirements.txt pyyaml python-dotenv
```

### **Step 4: Configure API keys**

The agent-side LLM API goes through Vault (see [INSTALL.md](INSTALL.md) §1 / §3). **In addition**, some tasks' environment containers need the keys in the table below — they are read as `${VAR}` by `docker-compose.yaml` / `task.toml` from the shell that runs `harbor run`, so they must be `export`ed (or written into the project-root `.env`). A missing key only fails the corresponding task; the other ~80 tasks need no external keys.

| Environment variable | Tasks involved |
| --- | --- |
| `GH_TOKEN` | gh-repo-analytics |
| `GITHUB_TOKEN` | fix-build-agentops |
| `ELEVENLABS_API_KEY` | pg-essay-to-audiobook |
| `OPENAI_API_KEY` | pedestrian-traffic-counting, pg-essay-to-audiobook |
| `OPENAI_BASE_URL` | pg-essay-to-audiobook |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | pedestrian-traffic-counting |
| `HUGGINGFACE_API_TOKEN` + `LLM_MODEL` | scheduling-email-assistant |

> ⚠️ Set `LLM_MODEL` to `meta-llama/Llama-3.2-1B-Instruct` (the task's built-in default 3B is deprecated; not overriding it will fail).

---

## **Running**

```bash
python examples/run_skillsbench_harbor.py --config configs/main-with-skills-agentos.yaml
```

- On the first run the config has `force_build: true`, so each image takes a few minutes to build; switch it to `false` afterward to reuse the cache and speed things up significantly.
- Results are written to `workdir/skillsbench/<job_name>/`.

---

## **FAQ**

| Symptom | Troubleshooting |
| --- | --- |
| Harbor reports `unknown shorthand flag: 'p' in -p` | Docker Compose is not V2 — install the Compose V2 plugin per "Environment setup, Step 2" |
| A task fails outright, log missing key / 401 | The task needs an external key from the "Step 4" table — confirm it is `export`ed or in `.env` |
| `scheduling-email-assistant` fails | Most likely `LLM_MODEL=meta-llama/Llama-3.2-1B-Instruct` is not set (the default 3B is deprecated) |
| Images rebuild repeatedly, very slow | Set `force_build` to `false` in the config to reuse the cache |
| Agent side can't read the LLM key | Check Vault: `.env`'s `VAULT_TOKEN` / `VAULT_ADDR`, and whether `vault status` is unsealed |

---

## **Key file locations**

| Path | Description |
| --- | --- |
| examples/run_skillsbench_harbor.py | Evaluation entry (Harbor mode) |
| configs/bus_skillsbench.py | SkillsBench agent / bus config |
| `datasets/AgentEvolver_SkillsBench/tasks` | Task data (with skills; use `tasks-no-skills` without skills) |
| `datasets/AgentEvolver_SkillsBench/harbor` | Harbor framework (installed via `uv pip install -e`) |
| `workdir/skillsbench/<job_name>/` | Results files (evaluation artifacts) |

For more on tuning and result analysis, see the README of the [SkillsBench](https://github.com/DVampire/AgentEvolver_SkillsBench) repo.
