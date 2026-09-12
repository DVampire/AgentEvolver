"""Website experiments with callable Skill, Agent and Connector evolution.

The builder owns the browser, product, evaluation and publication. A bounded
general reasoning consumer provides a baseline for evolved specialist agents;
neither is a simulated visitor. New components are authored during the task.
"""
from mmengine.config import read_base

with read_base():
    from .website_evolution_demo import *  # noqa: F403
    from .agents.general_agent import general_agent

agent_names = ["website_builder_agent", "general_agent"]
general_agent.update(
    model_name=model_name, enable_evolving=False, permission_mode="read_only",
    use_memory=False, use_plan=False, env_names=[], max_step=12, max_token=80_000,
    timeout=600, max_actions=1,
    capability_allowlists=dict(tool=["done_tool"], skill=[], agent=[], connector=[],
                              environment=[], workflow=[], plugin=[], memory=[]),
)
website_builder_agent.update(
    include_agents=True, accepts_evolved=["agent"],
    capability_allowlists={"environment": ["job", "browser_environment"],
                           "agent": ["general_agent"]},
)
