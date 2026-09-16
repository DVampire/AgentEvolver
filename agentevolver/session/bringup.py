"""Bring every manager up, once, in the order they depend on each other.

This sequence is written out in ten launchers under `examples/`, each a copy that can
drift from the others — a manager added here has to be remembered nine more times, and a
launcher that forgets one fails much later as a missing capability rather than a missing
line. Nothing forced them to converge, so they have not.

This is that sequence as one function. The launchers are not migrated here — each carries
its own argument handling and cleanup, and rewriting all ten at once is a change with far
more surface than value — but new callers use this rather than adding an eleventh copy,
and a launcher can move over one at a time.

Order is the content: version before anything that reads a version, trace and hooks
before the managers whose events they observe, models before agents, and extensions last
so a hot-plugged component layers over a built-in rather than under it.
"""

import json
from typing import Any, Dict, List, Optional

from agentevolver.agent import agent_manager
from agentevolver.config import config
from agentevolver.connector import connector_manager
from agentevolver.environment import environment_manager
from agentevolver.extension import extension_manager
from agentevolver.hook import hook_manager
from agentevolver.logger import logger
from agentevolver.memory import memory_manager
from agentevolver.model import model_manager
from agentevolver.plugins import plugin_manager
from agentevolver.prompt import prompt_manager
from agentevolver.skill import skill_manager
from agentevolver.tool import tool_manager
from agentevolver.trace import trace_manager
from agentevolver.trajectory import trajectory_manager
from agentevolver.version import version_manager
from agentevolver.workflow import workflow_manager


async def bring_up(*, plugins: Optional[List[str]] = None, quiet: bool = False) -> Dict[str, Any]:
    """Initialize every manager against the already-initialized `config`.

    The caller owns `config.initialize(...)` and session binding before this runs, because
    which config and which session are the caller's decisions; everything after them is
    the same for every caller, which is why it lives here.

    Args:
        plugins: Plugin allowlist for this run. `None` falls back to the config's, because
            which plugins a run may call is a property of the run, not of the config.
        quiet: Skip the per-manager inventory lines. A benchmark harness runs this once
            per task and the inventory is identical every time.

    Returns:
        What was loaded, by kind — enough to log or assert against without asking each
        manager again.
    """
    from agentevolver.config import validate_assembly

    for problem in validate_assembly(config):
        # Duplicate whitelist entries shadow each other silently; say so rather than
        # letting the second definition simply not exist.
        logger.warning(f"| ⚠️ Config: {problem}")

    await version_manager.initialize()

    await trace_manager.initialize()
    await trace_manager.start()
    await trajectory_manager.initialize()
    await hook_manager.initialize()

    await model_manager.initialize()
    await prompt_manager.initialize()
    await memory_manager.initialize(memory_names=config.memory_names)
    await tool_manager.initialize(tool_names=getattr(config, "tool_names", None))
    await skill_manager.initialize(skill_names=getattr(config, "skill_names", None))
    await connector_manager.initialize(connector_names=getattr(config, "connector_names", None))

    env_names = getattr(config, "env_names", None)
    if env_names:
        await environment_manager.initialize(env_names=env_names)

    await plugin_manager.initialize(
        plugin_names=plugins if plugins is not None else getattr(config, "plugin_names", [])
    )
    await agent_manager.initialize(agent_names=config.agent_names)
    await workflow_manager.initialize(workflow_names=getattr(config, "workflow_names", []))

    # Last, so a hot-plugged component layers over a built-in rather than under it.
    # Pointing it at the configured root belongs here, beside the initialization it
    # governs: `config.extension_root` can name a writable tree precisely because the
    # repository's own `extension/` may not be — a shared checkout can have it owned by
    # another account — and a caller that set the config but forgot this line still wrote
    # to the default and died on a manifest it could not open.
    extension_manager.set_base_dir(config.extension_root)
    manifest = await extension_manager.initialize()

    loaded = {
        "models": model_manager.list(),
        "tools": await tool_manager.list(),
        "skills": await skill_manager.list(),
        "connectors": await connector_manager.list(),
        "environments": await environment_manager.list() if env_names else [],
        "plugins": await plugin_manager.list(),
        "agents": await agent_manager.list(),
        "workflows": workflow_manager.list(),
        "extensions": [f"{c.module}:{c.name}" for c in manifest.components],
    }
    if not quiet:
        for group, names in loaded.items():
            logger.info(f"| ✅ {group.capitalize()}: {names}")
        logger.info(f"| 📋 All versions: {json.dumps(await version_manager.list(), indent=4)}")
    return loaded
