"""Config for running MonitorAgent standalone (without MetaAgent).

Usage
-----
# Default demo command (sleep 90, reports twice before finishing)
python examples/run_monitor_agent.py

# Custom command
python examples/run_monitor_agent.py --command "bash my_long_job.sh"

# Override poll interval
python examples/run_monitor_agent.py --cfg-options monitor_agent.poll_interval=10
"""

from mmengine.config import read_base

with read_base():
    from .base import max_tokens, window_size
    from .agents.monitor_agent import monitor_agent

tag         = "monitor_agent"
project_root = f"output/{tag}"
log_root = f"{project_root}/log"
workspace_root = f"{project_root}/workspace"
log_path    = "monitor_agent.log"

model_name      = "openrouter/claude-opus-4.8"

agent_names  = ["monitor_agent"]
tool_names   = []
skill_names  = []
connector_names = []
memory_names = []

monitor_agent.update(
    base_dir = workspace_root,
    enable_evolving = False,
)
