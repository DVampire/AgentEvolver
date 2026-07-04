"""Test whether a skill's description triggers correctly, by simulating skill-selection."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from src.tool.types import Tool
from src.response.types import Response, ResponseType
from src.registry import TOOL
from src.logger import logger

_DESCRIPTION = "Test whether a skill's description triggers correctly: given the skill and a set of labeled queries, simulate the agent's skill-selection and report per-query hits/misses/false-triggers plus overall accuracy."

_INSTRUCTION = """
## Function
Evaluate how well a skill's `description` triggers, the way our framework actually triggers skills: an agent sees the loaded skills' name+description and picks which (if any) to invoke for a query. This tool simulates that choice for each query and scores it against a should_trigger label — the objective signal for optimizing a description.

## Guidance
- Use this in the description-optimization loop: generate realistic should-trigger AND should-not-trigger queries (near-misses matter most), run this tool, then revise the description to fix the misses and re-run.
- It presents the target skill alongside a few distractor skills (other registered skills, or ones you pass) and asks a model which skill it would invoke — exactly the decision a real agent makes from skill_context.
- A high should_trigger hit-rate + low false-trigger rate on should-not-trigger queries means the description is well-targeted.

## Parameters
- name (str): the target skill to evaluate.
- queries (list[dict]): each `{"query": "<user request>", "should_trigger": true|false}`.
- distractors (list[str], optional): skill names to show alongside the target as competition. Defaults to a few other registered skills.

## Example
{"name": "skill_trigger_runner", "args": {"name": "my_skill", "queries": [{"query": "help me do X with file Y", "should_trigger": true}, {"query": "a near-miss request", "should_trigger": false}]}}
"""


class _TriggerChoice(BaseModel):
    """Which single skill the model would invoke for a request (or 'none')."""
    skill: str = Field(description="Exact name of the skill to invoke, or 'none' if no skill applies.")


@TOOL.register_module(force=True)
class SkillTriggerRunnerTool(Tool):
    """Simulate skill-selection to measure a skill description's triggering accuracy."""

    name: str = "skill_trigger_runner"
    description: str = _DESCRIPTION
    instruction: str = _INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    require_grad: bool = Field(default=False, description="Whether the tool requires gradients")
    model_name: str = Field(default="openrouter/gemini-3-flash-preview", description="Model used to simulate skill selection")
    max_distractors: int = Field(default=4, description="How many other skills to show as competition")

    def __init__(self, require_grad: bool = False, **kwargs):
        super().__init__(require_grad=require_grad, **kwargs)

    async def __call__(
        self,
        name: str,
        queries: List[Dict[str, Any]],
        distractors: Optional[List[str]] = None,
        **kwargs,
    ) -> Response:
        """Run the trigger evaluation.

        Args:
            name (str): the target skill name.
            queries (list[dict]): [{"query": str, "should_trigger": bool}, ...].
            distractors (list[str], optional): competing skill names to show alongside the target.
        """
        from src.skill.server import skill_manager
        from src.model import model_manager

        target = await skill_manager.get_info(name)
        if target is None:
            available = await skill_manager.list()
            return Response(type=ResponseType.TOOL, success=False,
                message=f"Skill '{name}' not found. Available: {available}")
        if not queries:
            return Response(type=ResponseType.TOOL, success=False,
                message="`queries` must be a non-empty list of {'query', 'should_trigger'} items.")

        # Assemble the competition set: target + a few distractor skills (name + description).
        all_names = await skill_manager.list()
        if distractors:
            chosen = [d for d in distractors if d in all_names and d != name]
        else:
            chosen = [n for n in all_names if n != name][: self.max_distractors]
        catalog = [target]
        for d in chosen:
            info = await skill_manager.get_info(d)
            if info is not None:
                catalog.append(info)

        skills_block = "\n".join(f"- {c.name}: {c.description}" for c in catalog)

        results = []
        hits = misses = false_triggers = correct_negatives = errors = 0
        for item in queries:
            q = item.get("query", "")
            should = bool(item.get("should_trigger"))
            prompt = (
                "You are an agent choosing whether to invoke a skill for a user request. "
                "Available skills (name: description):\n"
                f"{skills_block}\n\n"
                f"User request: {q}\n\n"
                "Which single skill would you invoke to best help with this request? "
                "Reply with the exact skill name, or 'none' if no skill is a good fit and you'd just handle it directly."
            )
            try:
                resp = await model_manager(
                    name=self.model_name,
                    input={"messages": [{"role": "user", "content": prompt}], "response_format": _TriggerChoice},
                )
                selected = (resp.parsed_model.skill if resp.parsed_model else "none").strip()
            except Exception as e:
                selected = f"<error: {e}>"
                errors += 1

            triggered = (selected == name)
            ok = (triggered == should)
            if ok and should:
                hits += 1
            elif ok and not should:
                correct_negatives += 1
            elif not ok and should:
                misses += 1
            else:
                false_triggers += 1

            results.append({
                "query": q,
                "should_trigger": should,
                "selected": selected,
                "triggered": triggered,
                "correct": ok,
            })

        n = len(queries)
        accuracy = (hits + correct_negatives) / n if n else 0.0
        data = {
            "skill": name,
            "distractors": chosen,
            "n_queries": n,
            "accuracy": round(accuracy, 3),
            "hits": hits,               # should-trigger and did
            "misses": misses,           # should-trigger but didn't (under-triggering)
            "false_triggers": false_triggers,  # should-not-trigger but did (over-triggering)
            "correct_negatives": correct_negatives,
            "errors": errors,
            "results": results,
        }
        msg = (
            f"Trigger accuracy for '{name}': {accuracy:.0%} ({hits + correct_negatives}/{n}). "
            f"Misses (under-trigger): {misses}; False triggers (over-trigger): {false_triggers}."
        )
        return Response(type=ResponseType.TOOL, success=True, message=msg, data=data)
