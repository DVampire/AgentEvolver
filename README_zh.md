# AgentEvolver

一个自进化的多智能体框架。由 **MetaAgent** 编排各子智能体来完成用户任务，同时 optimizer / evaluator / generator 等智能体持续改进工具、技能与智能体生态。

> 🌐 English version: [README.md](README.md)

## 安装

```bash
bash scripts/install.sh
```

它会创建 conda 环境（`agentos`，Python 3.12）、安装本包及其依赖、安装 Node.js，
并生成 `.env` 模板。重复执行是安全的。可加 `--extras browser` 安装浏览器自动化、
`--uv` 改用 uv、`--help` 查看全部选项。

然后在项目根目录的 `.env` 里填入 API Key：

```bash
ANTHROPIC_API_BASE='...'
ANTHROPIC_API_KEY='...'
OPENROUTER_API_BASE='...'
OPENROUTER_API_KEY='...'
```

密钥也可以交由 **Vault** 集中管理；只要 Vault 已配置且可连通，框架会优先从中读取，
否则自动回退到 `.env`。

手动安装、Vault、可选 extras 等完整说明：
**➡️ [scripts/INSTALL_zh.md](scripts/INSTALL_zh.md)**

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

- **输出目录** —— 每次运行都是独立 session：运行产物、日志和任务视图都写到 `output/<tag>/<session-id>/` 下（`workspace/` 存智能体的工作文件，`log/` 存日志和渲染后的任务视图）。
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
`./output/<tag>/<session-id>/`。可持久化的项目扩展位于与 `output/` 平级的
`./extension/`；会话先在自己的输出目录内暂存扩展，只有显式提升才会写入此目录。用户级状态（覆盖、缓存、暂存、deploy 注册表）
存放在项目根目录的 `./.agentevolver/`。可通过
`AGENTEVOLVER_HOME` 指定其他位置。每个 Gateway、CLI 和 TUI session 都有独立的
项目目录，其中 workspace 与暂存 extension 相互隔离；服务级元数据和已 promote 的
extension 则保留在 AgentEvolver home 目录中供各 session 共享。
