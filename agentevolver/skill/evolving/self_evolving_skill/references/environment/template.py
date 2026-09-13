"""TEMPLATE — an environment (a stateful class exposing named actions).

Copy to `{extension_root}/environment/{name}/environment.py`, rename the class, and
implement the actions. Pair it with an `ENVIRONMENT.md` manifest (see
`template-manifest.md`) in the same directory, plus an `__init__.py` that
imports the class so it registers on load.

The Manager gives each Agent its own instance and orders that Agent's actions.
Write business methods and keep state on self; no owner maps, locks or runtime calls.
Start resources in initialize and release them in cleanup. For independent evaluations,
see environment.md: state_scope="call" gives each invocation a fresh instance.
"""

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment
from agentevolver.logger import logger
from agentevolver.registry import ENVIRONMENT


@ENVIRONMENT.register_module(force=True)
class MyEnvironment(Environment):
    """One-line purpose — what the environment provides and its actions."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="my_environment")
    description: str = Field(default="What the environment is and when to use it.")
    metadata: Dict[str, Any] = Field(default={})
    enable_evolving: bool = Field(default=True)

    def __init__(self, base_dir: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        if base_dir:
            self.base_dir = base_dir
        # Lightweight in-memory state here; heavy resources go in initialize().
        self._state: Dict[str, Any] = {}

    # ---------------------------------------------------------------- lifecycle
    async def initialize(self) -> None:
        """Start resources for this owner binding, once before its first use."""
        logger.info(f"| 🌱 {self.name} ready")

    async def cleanup(self) -> None:
        """Release this binding's resources on owner exit or module cleanup."""
        self._state.clear()
        logger.info(f"| 🧹 {self.name} cleaned up")

    async def get_state(self, ctx=None, **kwargs) -> Dict[str, Any]:
        """Return compact current state for the environment context."""
        return {"success": True, "state": {"keys": sorted(self._state),
                "available_actions": ["set_value", "get_value"],
                "prerequisites": {"get_value": "key must have been stored by set_value"}}}

    # ---------------------------------------------------------------- actions
    @environment_manager.action(
        name="set_value",
        description="Store a value under a key. Args: key (str), value (str).",
        read_only=False, destructive=False, idempotent=True, open_world=False,
    )
    async def set_value(self, key: str, value: str, **kwargs) -> Dict[str, Any]:
        self._state[key] = value
        return {"success": True, "message": f"set {key}={value}", "data": {"key": key, "value": value}}

    @environment_manager.action(
        name="get_value",
        description="Read a previously stored key; call set_value first for a missing key. Args: key (str).",
        read_only=True, destructive=False, idempotent=True, open_world=False,
    )
    async def get_value(self, key: str, **kwargs) -> Dict[str, Any]:
        if key not in self._state:
            return {"success": False, "message": f"Unknown key {key!r}; call set_value before get_value.",
                    "data": {"code": "missing_key", "key": key, "next_action": "set_value"}}
        value = self._state[key]
        return {"success": True, "message": f"{key}={value}", "data": {"key": key, "value": value}}
