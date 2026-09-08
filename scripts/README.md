# 启动入口

`run.sh` 是 `examples/run_*.py` 的统一入口。它只选择入口和 Python 环境、切换到仓库根目录并透传参数；Agent 初始化、实验流程、输出路径和监控仍由对应 example 和框架 manager 管理。

```bash
# 查看所有入口
scripts/run.sh --list

# 宇宙网站实验
scripts/run.sh website_evolution_demo \
  --scenario-dir examples/tasks/website_evolution/orbital_simulator

# 仅检查配置，不运行 Agent
scripts/run.sh website_evolution_demo \
  --scenario-dir examples/tasks/website_evolution/orbital_simulator --validate-only

# 任意 example 的参数帮助
scripts/run.sh swebench_pro --help
scripts/run.sh examples/run_factor_mining.py --help

# 显式选择 Python
scripts/run.sh --python /path/to/env/bin/python meta_agent --task 'Build a website'
```

也可以用 `AGENTEVOLVER_PYTHON` 指定解释器。默认依次选择已激活的 virtualenv/非 base conda 环境、仓库 `.venv`、常见安装位置的 `agentos` 环境，最后使用 PATH 中的 Python。Conda 的 base 环境不会优先于项目环境。脚本不安装依赖，也不读取或打印凭据。Python 所在的 bin 目录会进入 PATH，以便启动同环境的 Node 等工具。

入口之后的参数全部交给 example；例如 `scripts/run.sh --help` 查看启动脚本帮助，`scripts/run.sh swebench_pro --help` 查看 SWE-bench Pro 参数。相对任务和配置路径以仓库根目录为基准。脚本通过 `exec` 保留信号与退出码，默认前台运行；需要后台运行时使用 tmux 或 nohup。

其他脚本的职责保持独立：

- `run-in-sandbox.sh`：容器执行入口，可组合为 `scripts/run-in-sandbox.sh -- scripts/run.sh meta_agent ...`。
- `serve-ui.sh`：启动应用后端和前端。
- `install.sh`：安装运行环境与依赖。

监控参数继续使用 example 自己的接口。支持监控的实验通过统一网关 `9876` 访问，内部监控端口由现有监控服务分配和注册。
