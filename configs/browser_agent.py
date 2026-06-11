from mmengine.config import read_base
with read_base():
    from .base import memory_config, window_size, max_tokens
    from .agents.browser_agent import browser_agent
    from .memory.file_system_memory import file_system_memory

tag = "browser_agent"
work_dir = f"work_dir/{tag}"
default_dir = f"work_dir/{tag}/default"
log_path = "agent.log"

use_local_proxy = True
version = "0.1.0"
model_name = "int_openrouter/gemini-3.1-pro-preview"

memory_names = [
    "file_system_memory",
]
agent_names = [
    "browser_agent",
]
# Pure environment agent — no tools; the task ends via the built-in `finish` action.
tool_names = []
skill_names = []
env_names = [
    "browser_environment",
]

#-----------------BROWSER ENVIRONMENT CONFIG-----------------
# base_dir is joined onto default_dir by config.process_environments
# → default/environment/browser; screenshots go to screenshots/<session_id>/
browser_environment = dict(
    base_dir="environment/browser",
    headless=True,
    viewport=dict(width=1024, height=768),
    use_sandbox=False,
    use_som=True,
    state_detail="elements",  # "elements" or "html"
    max_state_elements=0,  # 0 = no truncation (show all interactive elements)
    command_timeout=30.0,
)

#-----------------MEMORY SYSTEM CONFIG-----------------
file_system_memory.update(
    base_dir="memory/file_system",
    model_name=model_name,
    require_grad=False,
)

#-----------------BROWSER AGENT CONFIG-----------------
browser_agent.update(
    base_dir=work_dir,
    model_name=model_name,
    memory_name=memory_names[0],
    require_grad=False,
    use_memory=True,
)
