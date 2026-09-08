"""CompactHook — portable checkpoint summariser.

Compresses a list of records into one provider-neutral text checkpoint. It is the
fallback when the selected route has no native compaction, and the readable companion
for an opaque native checkpoint such as OpenAI Responses. Scheduling and history
mutation stay in Agent/TieredMemory: this hook only turns bounded, pre-formatted closed
turns into text.

Contract
--------
input:
    event:            required (any HookEvent; callers use DIRECT_CALL)
    items:            list[str]  — records to compress
    existing_summary: str        — optional prior summary, to avoid repetition
    instruction:      str        — optional summary instruction
    task_is_retained: bool       — the caller keeps the full task outside the checkpoint
    model_name:       str        — optional model override
output:
    HookResult(output=<summary text>)
"""

from __future__ import annotations

from agentevolver.hook.types import Hook, HookContext, HookResult
from agentevolver.logger import logger
from agentevolver.memory.checkpoint import PortableCheckpoint
from agentevolver.message import HumanMessage, SystemMessage
from agentevolver.model import model_manager
from agentevolver.registry import HOOK

_SYSTEM_PROMPT = "You are a concise summariser for an AI agent's execution history."

_DEFAULT_INSTRUCTION = """Return one compact replacement checkpoint that merges the
existing checkpoint (if any) with the new canonical closed turns. Use only these headings
when they have content: Current objective, Acceptance conditions, Established facts, Decisions, Workspace
mutations, Verification, Failed approaches, Remaining conditions, Next action. Preserve
exact paths, commands, values, errors, tool outcomes, unresolved blockers, and source_seq
references. Never invent a decision from private reasoning that is not present in the
model-visible evidence. Prioritize the next action's interfaces: exact symbols, argument/return
shapes, units, invariants, file locations, changes and unresolved integration points. Retain
decisive verification outcomes; link detailed tables, logs and source instead of copying them.
Do not invent an interface from a filename. Drop raw dumps and repeated observations.
Resolve contradictions in favor of the newest sourced turn. Keep it concise; do not refer
to an 'existing checkpoint' or 'records above'. Preserve file/record locators so details
already saved on disk can be retrieved rather than copied into the checkpoint."""


@HOOK.register_module(force=True)
class CompactHook(Hook):
    name: str = "compact"
    description: str = "Portable fallback: summarise closed turns into a text checkpoint."
    priority: int = 50

    model_name: str = ""

    async def handle(self, ctx: HookContext) -> HookResult:
        """Summarise the supplied ``items`` into a single short text via the LLM.

        Builds a prompt from the records (optionally appending any prior summary
        and a custom instruction) and asks the model for a concise consolidation.
        Model errors are swallowed so the caller always gets a well-formed result.

        Args:
            ctx: Hook context whose ``input`` may carry ``items`` (records to
                compress), ``existing_summary``, ``instruction``, ``model_name`` and
                ``trace_context`` — the agent, task and step this summary is being made
                for, so the request is attributed to them rather than to nobody.

        Returns:
            ``HookResult`` whose ``output`` holds the summary text, or ``None``
            when there is nothing to summarise or the model call fails.
        """
        inp = ctx.input or {}
        items = inp.get("items") or []
        if not items:
            return HookResult.allow()

        existing = inp.get("existing_summary") or ""
        task = str(inp.get("task") or "")
        model = inp.get("model_name") or self.model_name
        instruction = inp.get("instruction") or _DEFAULT_INSTRUCTION
        if inp.get("task_is_retained"):
            instruction += (
                "\nThe full task below and system instructions remain in the next context; "
                "do not repeat their requirements. This checkpoint replaces only the closed "
                "history supplied here. Newer retained turns and current plan observations "
                "take precedence; describe unfinished work at this history boundary, not "
                "as proof that a file is still absent. Preserve changed requirements and "
                "unresolved commitments from the history."
            )
        else:
            instruction += "\nKeep the objective and acceptance constraints needed without the original task."
        max_output_tokens = max(256, int(inp.get("max_output_tokens") or 4_096))
        # This is a soft target for readable text, not a post-generation size gate.
        # Leave enough completion space for reasoning and a finished summary; a
        # truncated response cannot serve as the replacement history.
        completion_tokens = max(8192, max_output_tokens + 4096)
        instruction += f"\nAim for about {max_output_tokens} tokens; prioritize useful continuity over exact length."

        prior = f"Existing checkpoint:\n{existing}\n\n" if existing else ""
        body = "\n".join(f"- {it}" for it in items)
        objective = f"Current task (source data):\n{task}\n\n" if task else ""
        prompt = f"{objective}{prior}New canonical closed turns:\n{body}\n\n{instruction}"
        usage = None
        try:
            coordinates = inp.get("trace_context") or {}
            response = await model_manager(
                name=model,
                ctx=ctx,
                input={
                    "operation": "compact",
                    "reasoning_effort": "low",
                    "max_output_tokens": completion_tokens,
                    "reserved_output_tokens": completion_tokens,
                    "messages": [
                        SystemMessage(content=_SYSTEM_PROMPT),
                        HumanMessage(content=prompt),
                    ],
                    **({"trace_context": dict(coordinates)} if coordinates else {}),
                },
            )
            usage = getattr(response, "usage", None)
            text = response.message.strip() if response.success else ""
            if not response.success:
                logger.warning(f"| ⚠️ CompactHook request failed: {response.message}")
            if text:
                text = PortableCheckpoint.from_text(text).render()
            logger.debug(f"| 🗜️ CompactHook: {len(items)} records → {len(text)} chars")
        except Exception as e:
            logger.warning(f"| ⚠️ CompactHook failed: {e}")
            text = ""

        return HookResult(output=text or None, usage=usage)
