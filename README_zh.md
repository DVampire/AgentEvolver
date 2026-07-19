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
   pip install -e ".[browser]" && python -m playwright install chromium
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
- **输出目录** —— 运行产物、任务视图和日志都写到 `workspace_root/meta_agent/` 下（`run/` 存运行状态，`workspace/` 存智能体的工作文件）。
- 任务结束时，日志会打印最终结果；若生成了记忆报告，还会打印其 HTML 路径。

现成的任务文档在 [`examples/tasks/`](examples/tasks/) 下，可参考它们了解任务的写法。

## 交互式网页前端

[`frontend/`](frontend/) 是基于 React/Vite 的浏览器界面；AgentEvolver 的 Python 运行时继续作为后端。页面通过版本化 Gateway 协议连接 WebSocket，提供任务输入、实时 Agent 活动、取消任务和事件详情查看。

```bash
# 终端 1：启动 Python 后端
conda activate agent
agentevolver serve --transport websocket --host 127.0.0.1 --port 9876

# 终端 2：启动浏览器前端
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`（或 Vite 输出的地址）。页面默认连接 `ws://127.0.0.1:9876/ws`，可通过侧栏的 **Connection** 修改地址或填写 token。

若服务不在受信任的本地网络，请先设置 `AGENTEVOLVER_GATEWAY_TOKEN`。WebSocket 客户端会自动重连，并请求服务端回放断开期间遗漏的会话事件。完整说明见 [`frontend/README.md`](frontend/README.md)。

绑定非本机地址时 Gateway 会强制要求 token。浏览器来源还可以通过重复的
`--allow-origin https://your-ui.example` 显式限制。受限的 `bash_tool` 只会在隔离
sandbox 中执行；可信的本地调试若确实需要直接执行主机命令，必须把该工具显式配置为
`danger_full_access`。

## 终端命令

`agentevolver` 只有一个 CLI，提供三种模式：直接执行控制命令（例如
`agentevolver /registry`）、进入终端交互循环（`agentevolver tui`），或启动
Gateway（`agentevolver serve ...`）。

生成的运行产物默认存放在当前项目的
`./output/<tag>/sessions/<session-id>/`。可持久化的项目扩展位于与 `output/` 平级的
`./extension/`；会话先在自己的输出目录内暂存扩展，只有显式提升才会写入此目录。用户级覆盖和缓存等状态存放在
`~/.agentevolver`。可通过
`AGENTEVOLVER_HOME` 指定其他位置。每个 Gateway、CLI 和 TUI session 都有独立的
项目目录，其中 workspace 与暂存 extension 相互隔离；服务级元数据和已 promote 的
extension 则保留在 AgentEvolver home 目录中供各 session 共享。
