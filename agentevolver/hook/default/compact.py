"""CompactHook — portable checkpoint summariser.

Compresses a list of records into one provider-neutral text checkpoint. It is the
fallback when the selected route has no native compaction, and the readable companion
for an opaque native checkpoint such as OpenAI Responses. Scheduling and history
mutation stay in Agent/TieredMemory: this hook only turns bounded, pre-formatted closed
turns into text.

Contract
--------
input:
    event:            required (any HookEvent; callers use ON_CALL)
    items:            list[str]  — records to compress
    existing_summary: str        — optional prior summary, to avoid repetition
    instruction:      str        — optional summary instruction
    model_name:       str        — optional model override
output:
    HookResult(output=<summary text>)
"""

from __future__ import annotations

from agentevolver.registry import HOOK
from agentevolver.logger import logger
from agentevolver.message import SystemMessage, HumanMessage
from agentevolver.model import model_manager
from agentevolver.hook.types import HookContext, HookResult, Hook


_SYSTEM_PROMPT = "You are a concise summariser for an AI agent's execution history."

_DEFAULT_INSTRUCTION = """Return one compact replacement checkpoint that merges the
existing checkpoint (if any) with the new canonical closed turns. Use only these headings
when they have content: Current objective, Established facts, Decisions, Workspace
mutations, Verification, Failed approaches, Remaining conditions, Next action. Preserve
exact paths, commands, values, errors, tool outcomes, unresolved blockers, and source_seq
references. Never invent a decision from private reasoning that is not present in the
model-visible evidence. Drop raw dumps and repeated observations. Resolve contradictions in
favor of the newest sourced turn. Keep the checkpoint under 2,000 words and make it stand
alone; do not refer to an 'existing checkpoint' or 'records above'."""


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
                compress), ``existing_summary``, ``instruction`` and ``model_name``.

        Returns:
            ``HookResult`` whose ``output`` holds the summary text, or ``None``
            when there is nothing to summarise or the model call fails.
        """
        inp = ctx.input or {}
        items = inp.get("items") or []
        if not items:
            return HookResult.allow()

        existing = inp.get("existing_summary") or ""
        model = inp.get("model_name") or self.model_name
        instruction = inp.get("instruction") or _DEFAULT_INSTRUCTION
        max_output_tokens = max(256, int(inp.get("max_output_tokens") or 2_048))

        prior = f"Existing checkpoint:\n{existing}\n\n" if existing else ""
        body = "\n".join(f"- {it}" for it in items)
        prompt = f"{prior}New canonical closed turns:\n{body}\n\n{instruction}"

        try:
            response = await model_manager(
                name=model,
                input={
                    "operation": "compact",
                    "max_output_tokens": max_output_tokens,
                    "reserved_output_tokens": max_output_tokens,
                    "messages": [
                        SystemMessage(content=_SYSTEM_PROMPT),
                        HumanMessage(content=prompt),
                    ],
                },
            )
            text = response.message.strip() if response.success else ""
            logger.debug(f"| 🗜️ CompactHook: {len(items)} records → {len(text)} chars")
        except Exception as e:
            logger.warning(f"| ⚠️ CompactHook failed: {e}")
            text = ""

        return HookResult(output=text or None)
