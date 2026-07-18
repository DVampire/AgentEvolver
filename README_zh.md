# AgentEvolver

一个自进化的多智能体框架。由 **MetaAgent** 编排各子智能体来完成用户任务，同时 optimizer / evaluator / generator 等智能体持续改进工具、技能与智能体生态。

> 🌐 English version: [README.md](README.md)

## 安装

完整的安装步骤（Vault 密钥管理器、Python 环境）详见：

**➡️ [scripts/INSTALL_zh.md](scripts/INSTALL_zh.md)**

简要流程：

1. **安装并配置密钥管理器 Vault** —— 本项目的 API Key 统一由 Vault 集中管理，而非明文写入 `.env`。见安装文档第 1 节。
2. **配置 Python 环境**（conda + pip 或 uv，详见安装文档；需要 Python 3.11+）：

   ```bash
   conda create -n agent python=3.12 && conda activate agent
   pip install -e .                      # 或用 uv：uv sync

   # 可选：浏览器自动化
   pip install -e ".[browser]" && playwright install && browser-use install
   ```

3. **配置项目根目录的 `.env`**，让框架能连上 Vault：

   ```bash
   VAULT_ADDR='http://127.0.0.1:8200'
   VAULT_TOKEN="<initial root token>"
   UNSEAL_TOKEN='<unseal token key1>'
   SECRET_ENGINE_PATH='cubbyhole/env'
   ```

## 运行 MetaAgent

入口脚本是 [`examples/run_meta_agent.py`](examples/run_meta_agent.py)，它会启动 MetaAgent 及其子智能体，并把单个任务跑到完成。

```bash
conda activate agent

# 1. 运行默认任务
python examples/run_meta_agent.py

# 2. 直接传入任务文本
python examples/run_meta_agent.py --task "写一个反转字符串的 Python 函数并补充单元测试。"

# 3. 从任务文档运行（examples/tasks/ 下的 .html / .md）
python examples/run_meta_agent.py --task-file examples/tasks/qsar_egfr_experiment.html
```

### 参数说明

| 参数 | 说明 |
| --- | --- |
| `--task "<文本>"` | 内联任务文本，优先级高于 `--task-file`。 |
| `--task-file <路径>` | 任务文档路径（`.html` / `.md`），位于 `examples/tasks/` 下。 |
| `--config <路径>` | 配置文件（默认：`configs/meta_agent.py`）。 |
| `--cfg-options key=value ...` | 覆盖任意配置项，例如 `--cfg-options model_name=openai/o3`。 |

### 运行产物

- **Trace UI** —— 运行时日志会打印 `🌐 Trace UI: http://localhost:<端口>`，打开即可实时观察各智能体的执行过程。
- **输出目录** —— 运行产物、任务视图和日志都写到 `work_dir/meta_agent/` 下（`run/` 存运行状态，`workspace/` 存智能体的工作文件）。
- 任务结束时，日志会打印最终结果；若生成了记忆报告，还会打印其 HTML 路径。

现成的任务文档在 [`examples/tasks/`](examples/tasks/) 下，可参考它们了解任务的写法。
