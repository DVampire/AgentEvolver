"""The class Harbor calls: one trial of one task, run by this framework's agent."""

import os
from argparse import Namespace
from pathlib import Path
from typing import Any, List, Optional

from harbor.agents.base import BaseAgent

from agentevolver.logger import logger
from agentevolver.utils import make_id

#: What an agent may spend on one Harbor task before the trial is abandoned. Harbor
#: enforces its own wall-clock timeout per task; this is the step budget, which Harbor
#: knows nothing about and which otherwise defaults to whatever the config happens to say.
DEFAULT_STEP_BUDGET = 120


class AgentEvolverAgent(BaseAgent):
    """Runs an AgentEvolver agent against a Harbor-provisioned task container.

    Harbor builds the container, hands over the instruction and the environment, and runs
    the task's verifier afterwards. This class does the middle part and nothing else: it
    brings the framework up, points its sandbox at Harbor's environment so every existing
    tool works unchanged, runs one agent to completion, and reports what the run cost.

    The environment is deliberately not ours. Provisioning our own container would score a
    setup the leaderboard never ran — which is exactly the failure `deep-swe` 1.1 moved
    grading into an isolated container to prevent.
    """

    def __init__(self, *args: Any, config_path: str = "", agent_name: str = "",
                 step_budget: Optional[int] = None, extension_root: str = "",
                 **kwargs: Any):
        super().__init__(*args, **kwargs)
        # Harbor passes `--model`; the rest comes from this framework's own config, since
        # which agent and which tools are exactly what a config file is for.
        self._config_path = config_path or os.environ.get(
            "AGENTEVOLVER_CONFIG", "configs/meta_agent.py"
        )
        self._agent_name = agent_name or os.environ.get("AGENTEVOLVER_AGENT", "meta_agent")
        self._step_budget = step_budget or int(
            os.environ.get("AGENTEVOLVER_STEP_BUDGET", DEFAULT_STEP_BUDGET)
        )
        self._extension_root = extension_root or os.environ.get("AGENTEVOLVER_EXTENSION_ROOT", "")
        self._sandbox: Any = None
        self._session: Any = None
        self._usage: dict = {}

    @staticmethod
    def name() -> str:
        return "agentevolver"

    def version(self) -> Optional[str]:
        from agentevolver import __version__

        return __version__

    def config_overrides(self) -> dict:
        """What this trial changes about the config, as `cfg_options` keys.

        `cfg_options` is the config's own override channel: dotted keys merged before the
        agent sections are processed, so an agent reads the overridden value at the moment
        it is built. Two earlier attempts were both wrong — `config.initialize` was called
        without its required `args`, and the step budget was passed as a call argument to
        `agent_manager`, where it merged into the agent's payload, matched no parameter,
        and was dropped without a word, so every trial silently ran at whatever the config
        file said. The field is `max_step`, singular.
        """
        overrides = {f"{self._agent_name}.max_step": self._step_budget}
        # A run writes evolved components into `extension_root`, and the repository's own
        # `extension/` is not always writable — a shared checkout can have it owned by
        # another account, and then every trial dies in setup on a manifest it cannot
        # open, long before the task is even read.
        if self._extension_root:
            overrides["extension_root"] = self._extension_root
        if self.model_name:
            # Harbor names the model on its command line, and that name is part of what a
            # leaderboard row means. It has to beat the config, or the flag would be a lie.
            overrides["model_name"] = self.model_name
            overrides[f"{self._agent_name}.model_name"] = self.model_name
        return overrides

    async def setup(self, environment: Any) -> None:
        """Bring the framework up and bind its sandbox to Harbor's container.

        Done here rather than in `run` because Harbor times setup separately from the
        agent's own work, and because a bring-up failure should surface as a broken agent
        rather than as a task the agent simply failed.
        """
        from agentevolver.config import config
        from agentevolver.sandbox.default.harbor import HarborSandbox
        from agentevolver.session.bringup import bring_up
        from agentevolver.session.context import bind_session_roots, ensure_session_sandbox
        from agentevolver.session.types import SessionContext

        config.initialize(
            config_path=self._config_path,
            args=Namespace(cfg_options=self.config_overrides()),
            verbose=False,
        )

        self._session = SessionContext(id=make_id(), name="harbor_trial")
        session_sandbox = ensure_session_sandbox(
            self._session, shared_extension_root=config.extension_root
        )
        bind_session_roots(config, session_sandbox)
        logger.initialize(config=config)

        await bring_up(quiet=True)

        # Every tool in this repository talks to `Sandbox` and nothing below it, so this
        # one line is what lets bash, patching and the rest run inside a Harbor task.
        self._sandbox = HarborSandbox(environment=environment)
        from agentevolver.sandbox import sandbox_manager

        sandbox_manager.adopt(self._sandbox, reuse_key="harbor")

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        """Run one agent to completion. The reward comes from Harbor's verifier, not here.

        Harbor takes no return value: what the trial is scored on is the state this leaves
        in the container, which is why nothing is reported back but usage.
        """
        from agentevolver.agent import agent_manager

        try:
            await agent_manager(
                name=self._agent_name,
                input={"task": instruction, "files": []},
                ctx=self._session,
            )
        except Exception as exc:
            # A crashed agent is a failed trial, not a broken harness: let Harbor grade
            # whatever is in the container rather than losing the whole run to a traceback.
            logger.warning(f"| ⚓ harbor trial failed: {type(exc).__name__}: {exc}")
        finally:
            self._usage = self._collect_usage()
            self.populate_context_post_run(context)

    def populate_context_post_run(self, context: Any) -> None:
        """Report tokens and cost, which Harbor records alongside the reward.

        This framework already measures all four per call, so leaving them unset would
        hide a real cost difference between agents that Harbor is built to show.
        """
        for field, value in self._usage.items():
            if value is not None and hasattr(context, field):
                setattr(context, field, value)

    def _collect_usage(self) -> dict:
        """Sum this session's usage from the trace the run already wrote.

        An empty event list is returned when this process is not holding the session's
        log — not when the session did nothing. Reporting zeros for that would put a free
        trial on Harbor's record, so nothing is reported instead: an absent cost reads as
        unknown, a zero reads as measured.
        """
        try:
            from agentevolver.trace import trace_manager

            events = trace_manager.events(session_id=getattr(self._session, "id", None))
        except Exception as exc:
            # Usage is reporting, not scoring. Never fail a graded trial over it.
            logger.warning(f"| ⚓ could not total harbor trial usage: {type(exc).__name__}: {exc}")
            return {}
        if not events:
            logger.warning("| ⚓ no trace events retained for this trial; usage not reported")
            return {}

        totals = {"n_input_tokens": 0, "n_cache_tokens": 0, "n_output_tokens": 0, "cost_usd": 0.0}
        for event in events:
            usage = getattr(event, "usage", None) or {}
            if hasattr(usage, "model_dump"):
                usage = usage.model_dump()
            totals["n_input_tokens"] += usage.get("input_tokens") or 0
            totals["n_cache_tokens"] += usage.get("cache_read_tokens") or 0
            totals["n_output_tokens"] += usage.get("output_tokens") or 0
            totals["cost_usd"] += usage.get("cost") or 0.0
        return totals

    @staticmethod
    def handoff(trial_dir: Path, cwd: Path) -> List[str]:
        """Files Harbor should keep from a trial. The trace is the run's own record."""
        return ["log/trace", "log/agent.log"]
