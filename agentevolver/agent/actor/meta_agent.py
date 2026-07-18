"""MetaAgent — a normal Agent that happens to orchestrate.

MetaAgent runs the EXACT same event-driven loop as every other agent (on_start →
_advance → _think → _dispatch_round → _run_one → _conclude). It is an orchestrator only
because two things differ, both via base-class seams:

  _include_agents      → True: registered sub-agents are projected into its roster, so it
                         can call them as capabilities (a sub-agent is just another tool).
  _handle_extra_event  → when a blocked sub-agent escalates (its inbox receives an
                         EscalationMessage), reply to it so it can continue.

Everything else — decomposition, concurrent round dispatch, finishing — is the ordinary
loop. There is no bespoke plan/round tracking, no reviewer gate in code (review is soft,
guided by the prompt), and no custom prompt: history comes from memory and the available
sub-agents appear in the roster, exactly like a leaf agent.

Escalation is the general runtime suspend/resume channel: a sub-agent's ``escalate_tool``
suspends it on its task_id and posts an EscalationMessage here; ``reply_tool`` resumes it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import Field

from agentevolver.agent.types import Agent, AgentContext
from agentevolver.response.types import Response
from agentevolver.logger import logger
from agentevolver.registry import AGENT
from agentevolver.protocol import protocol_manager, EscalationMessage


# ---------------------------------------------------------------------------
# MetaAgent
# ---------------------------------------------------------------------------


@AGENT.register_module(force=True)
class MetaAgent(Agent):
    """Orchestrator = a normal Agent with sub-agents in its roster (see module docstring)."""

    model_config = {"arbitrary_types_allowed": True, "extra": "allow"}

    name: str = Field(default="meta_agent")
    description: str = Field(
        default="Orchestrator that decomposes tasks, dispatches sub-agents concurrently, "
        "reacts to results, and triggers self-evolution when agents underperform."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    enable_evolving: bool = Field(default=False)

    def __init__(self, base_dir: str, prompt_name: Optional[str] = None, max_step: int = 50, **kwargs):
        super().__init__(base_dir=base_dir, prompt_name=prompt_name or "meta_agent", max_step=max_step, **kwargs)

    # The one thing that makes this agent an orchestrator: sub-agents in its roster.
    def _include_agents(self) -> bool:
        return True

    async def _handle_extra_event(self, run, msg: Any) -> None:
        """A blocked sub-agent escalated (its EscalationMessage landed in our inbox). Reply
        with a focused think turn — mid-round, so we do NOT dispatch new work; the round in
        flight keeps running. The reply resumes the suspended sub-agent over the escalation
        channel (``protocol_manager.reply`` → runtime.resume)."""
        if not isinstance(msg, EscalationMessage):
            return  # progress updates etc. — nothing to do

        note = (
            f"A sub-agent is BLOCKED and waiting for your reply (task_id={msg.task_id}, "
            f"agent={msg.agent_name}):\n{msg.text}\n\n"
            f"Call reply_tool now with this exact task_id and concrete, actionable guidance "
            f"(or tell it to stop gracefully). Do not start new work in this turn."
        )
        messages = await self._get_messages(
            run.task, ctx=run.ctx, files=run.files, step_number=run.step,
            action_errors=[note], constraint_status=[], _run=run,
        )
        decision = await self._think(messages, run.task_id, run.step, run.ctx, include_agents=True)
        guidance = next(
            (c.input.get("reply") for c in decision["tool_calls"]
             if c.name == "reply_tool" and (c.input or {}).get("task_id") == msg.task_id),
            None,
        ) or "No specific guidance available. Use your best judgement or stop gracefully."
        protocol_manager.reply(msg.task_id, guidance)
        logger.info(f"| 💬 MetaAgent replied to escalation [{msg.task_id}]")
