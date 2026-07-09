# SkillsBench 评测指南

本文档说明如何在 **SkillsBench（~84 个 Docker 容器化任务）** 上进行端到端评测。涵盖：链路原理、环境与密钥准备、首次运行、看进度与结果。

SkillsBench 是一个多轮交互式 benchmark：每个任务跑在独立的 Docker 容器里，Agent 发送 shell 命令、接收 stdout/stderr 反馈，最终由容器内的 `tests/test.sh` 评估并给出 reward（0.0–1.0）。

> 评测链路：YAML 配置 → Harbor CLI → `Super Agent` → 容器内 AgentEvolver bus → `tests/test.sh` 算 reward
>
> 任务数据 + Harbor 框架在独立仓库：https://github.com/DVampire/AgentEvolver_SkillsBench （调参 / 看结果的更多细节详见其 README）
>
> 主仓库：https://github.com/DVampire/AgentEvolver

> 🌐 English version: [intro_skillsbench.md](intro_skillsbench.md)

---

## **快速简介版（TL;DR）**

假设你已经按 [INSTALL_zh.md](INSTALL_zh.md) 配置好了 Vault、opencode、Python 环境：

```bash
# 0) 克隆任务 + Harbor 框架到 datasets/
cd datasets && git clone git@github.com:DVampire/AgentEvolver_SkillsBench.git && cd ..

# 1) 确认 Docker Compose V2（必须 v2.x）
docker compose version

# 2) 装 Harbor + 依赖
uv pip install --system -e datasets/AgentEvolver_SkillsBench/harbor
pip install -r scripts/requirements.txt pyyaml python-dotenv

# 3) 跑全量
python examples/run_skillsbench_harbor.py --config configs/main-with-skills-agentos.yaml
#    结果写在 workdir/skillsbench/<job_name>/
```

---

## **评测范围与产物**

- **任务量**：~84 个任务，每个任务一个独立 Docker 容器。
- **数据集**：默认 `tasks`（带 skills 元数据）；不注入 skills 用 `tasks-no-skills`。
- **每题工作流**：走 AgentEvolver 完整 Agent 流水线（容器内 bus），多轮 shell 交互直到任务结束或超步数。
- **评估方式**：任务结束后由容器内 `tests/test.sh` 给出 reward（0.0–1.0）。
- **产物**：每次运行在 `workdir/skillsbench/<job_name>/` 下生成逐任务记录与汇总。

---

## **环境准备**

Vault / opencode / Python 基础环境请先按 [INSTALL_zh.md](INSTALL_zh.md) 配置好。下面是 SkillsBench 特有的额外步骤。

### **Step 1：克隆数据仓库到 `datasets/`**

```bash
cd datasets && git clone git@github.com:DVampire/AgentEvolver_SkillsBench.git && cd ..
```

- 任务数据：`datasets/AgentEvolver_SkillsBench/tasks`（无 skills 版用 `tasks-no-skills`）
- Harbor 框架：`datasets/AgentEvolver_SkillsBench/harbor`

### **Step 2：Docker + Compose V2**

```bash
docker compose version    # 必须 v2.x，否则 Harbor 报 "unknown shorthand flag: 'p' in -p"
```

缺 Compose V2 插件时手动装：

```bash
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -L "https://github.com/docker/compose/releases/download/v2.29.2/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

### **Step 3：安装 Harbor + 依赖**

```bash
uv pip install --system -e datasets/SkillsAgentEvolver_SkillsBenchBench/harbor   # 没 uv：curl -LsSf https://astral.sh/uv/install.sh | sh
pip install -r scripts/requirements.txt pyyaml python-dotenv
```

### **Step 4：配置 API Key**

Agent 侧 LLM API 走 Vault（见 [INSTALL_zh.md](INSTALL_zh.md) §一 / §三）。**此外**部分任务的环境容器还需下表中的 key——它们由 `docker-compose.yaml` / `task.toml` 从运行 `harbor run` 的 shell 里读 `${VAR}`，所以必须 `export`（或写进项目根目录 `.env`）。缺失只会让对应任务失败，其余 ~80 个任务无需外部 key。

| 环境变量 | 涉及任务 |
| --- | --- |
| `GH_TOKEN` | gh-repo-analytics |
| `GITHUB_TOKEN` | fix-build-agentops |
| `ELEVENLABS_API_KEY` | pg-essay-to-audiobook |
| `OPENAI_API_KEY` | pedestrian-traffic-counting, pg-essay-to-audiobook |
| `OPENAI_BASE_URL` | pg-essay-to-audiobook |
| `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | pedestrian-traffic-counting |
| `HUGGINGFACE_API_TOKEN` + `LLM_MODEL` | scheduling-email-assistant |

> ⚠️ `LLM_MODEL` 设为 `meta-llama/Llama-3.2-1B-Instruct`（任务内置默认的 3B 已停用，不覆盖会失败）。

---

## **运行**

```bash
python examples/run_skillsbench_harbor.py --config configs/main-with-skills-agentos.yaml
```

- 首次运行配置里 `force_build: true`，每个镜像 build 需要几分钟；之后改成 `false` 复用缓存即可大幅加速。
- 结果写在 `workdir/skillsbench/<job_name>/`。

---

## **常见问题**

| 现象 | 排查 |
| --- | --- |
| Harbor 报 `unknown shorthand flag: 'p' in -p` | Docker Compose 不是 V2，按「环境准备 Step 2」装 Compose V2 插件 |
| 某任务直接失败、日志缺 key / 401 | 该任务需要「Step 4」表里的外部 key，确认已 `export` 或写进 `.env` |
| `scheduling-email-assistant` 失败 | 多半没设 `LLM_MODEL=meta-llama/Llama-3.2-1B-Instruct`（默认 3B 已停用） |
| 镜像反复重新 build、很慢 | 配置里 `force_build` 改成 `false` 复用缓存 |
| Agent 侧读不到 LLM key | 检查 Vault：`.env` 的 `VAULT_TOKEN` / `VAULT_ADDR`，`vault status` 是否 unsealed |

---

## **关键文件位置一览**

| 路径 | 说明 |
| --- | --- |
| examples/run_skillsbench_harbor.py | 评测入口（Harbor 模式） |
| configs/bus_skillsbench.py | SkillsBench Agent / bus 配置 |
| `datasets/AgentEvolver_SkillsBench/tasks` | 任务数据（带 skills；无 skills 用 `tasks-no-skills`） |
| `datasets/AgentEvolver_SkillsBench/harbor` | Harbor 框架（`uv pip install -e` 安装） |
| `workdir/skillsbench/<job_name>/` | 结果文件（评测产物） |

调参与结果分析的更多细节，请参考 [AgentEvolver_SkillsBench](https://github.com/DVampire/AgentEvolver_SkillsBench) 仓库的 README。
