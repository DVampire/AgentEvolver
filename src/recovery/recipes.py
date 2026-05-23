"""Standard recovery recipes and the per-session attempt ledger.

Port of recovery_recipes.rs from claw-code reference implementation.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from src.recovery.types import FailureScenario, RecoveryRecipe, RecoveryStep, RecoveryEvent


# ---------------------------------------------------------------------------
# Standard recipes (one per known failure scenario)
# ---------------------------------------------------------------------------

_RECIPES: Dict[FailureScenario, RecoveryRecipe] = {
    FailureScenario.TOOL_TIMEOUT: RecoveryRecipe(
        scenario=FailureScenario.TOOL_TIMEOUT,
        max_attempts=2,
        steps=[
            RecoveryStep(
                description="Retry the timed-out tool call with a shorter scope",
                instruction=(
                    "The previous tool call timed out. Try again with a narrower scope "
                    "(e.g. smaller directory, fewer lines, simpler query)."
                ),
            ),
            RecoveryStep(
                description="Fall back to a simpler alternative",
                instruction=(
                    "The tool is still timing out. Use a simpler alternative approach "
                    "that avoids the timed-out operation."
                ),
            ),
        ],
    ),
    FailureScenario.PERMISSION_DENIED: RecoveryRecipe(
        scenario=FailureScenario.PERMISSION_DENIED,
        max_attempts=1,
        steps=[
            RecoveryStep(
                description="Check permission mode and use an allowed alternative",
                instruction=(
                    "The previous operation was denied by the permission system. "
                    "Check what operations are allowed in the current mode and use "
                    "an equivalent approach within those constraints."
                ),
            ),
        ],
    ),
    FailureScenario.CONTEXT_OVERFLOW: RecoveryRecipe(
        scenario=FailureScenario.CONTEXT_OVERFLOW,
        max_attempts=1,
        steps=[
            RecoveryStep(
                description="Summarise and continue from current state",
                instruction=(
                    "Context limit is approaching. Summarise what has been accomplished "
                    "so far and continue with the most critical remaining work only."
                ),
            ),
        ],
    ),
    FailureScenario.STALE_BRANCH: RecoveryRecipe(
        scenario=FailureScenario.STALE_BRANCH,
        max_attempts=1,
        steps=[
            RecoveryStep(
                description="Re-read modified files before continuing",
                instruction=(
                    "The working branch may have changed. Re-read any files you were "
                    "about to modify to ensure you have the latest content."
                ),
            ),
        ],
    ),
    FailureScenario.PARSE_ERROR: RecoveryRecipe(
        scenario=FailureScenario.PARSE_ERROR,
        max_attempts=2,
        steps=[
            RecoveryStep(
                description="Re-read the file and retry with corrected syntax",
                instruction=(
                    "A parse error occurred. Re-read the file at the exact location "
                    "of the error and fix the syntax issue."
                ),
            ),
            RecoveryStep(
                description="Validate with a minimal reproduction",
                instruction=(
                    "The parse error persists. Create a minimal version of the change "
                    "and verify it parses correctly before applying."
                ),
            ),
        ],
    ),
    FailureScenario.MAX_STEPS_EXCEEDED: RecoveryRecipe(
        scenario=FailureScenario.MAX_STEPS_EXCEEDED,
        max_attempts=1,
        escalate_on_exhaust=True,
        steps=[
            RecoveryStep(
                description="Produce a partial result and escalate",
                instruction=(
                    "Step limit reached. Produce the best partial result you can "
                    "with work completed so far and clearly state what remains undone."
                ),
            ),
        ],
    ),
    FailureScenario.LLM_ERROR: RecoveryRecipe(
        scenario=FailureScenario.LLM_ERROR,
        max_attempts=2,
        steps=[
            RecoveryStep(
                description="Retry with simplified messages",
                instruction=(
                    "An LLM API error occurred. Simplify the current request and retry."
                ),
            ),
            RecoveryStep(
                description="Use fallback model",
                instruction=(
                    "The primary model keeps failing. Switch to a simpler approach "
                    "that requires fewer tokens."
                ),
            ),
        ],
    ),
    FailureScenario.UNKNOWN: RecoveryRecipe(
        scenario=FailureScenario.UNKNOWN,
        max_attempts=1,
        steps=[
            RecoveryStep(
                description="Report the error and attempt to continue",
                instruction=(
                    "An unexpected error occurred. Report what failed, then attempt "
                    "to continue from the last known-good state."
                ),
            ),
        ],
    ),
}


def get_recipe(scenario: FailureScenario) -> RecoveryRecipe:
    return _RECIPES.get(scenario, _RECIPES[FailureScenario.UNKNOWN])


# ---------------------------------------------------------------------------
# Per-session attempt ledger
# ---------------------------------------------------------------------------

class RecoveryLedger:
    """Tracks recovery attempts per session per scenario.

    Thread-safety: not async-safe; callers should hold a session lock.
    """

    def __init__(self) -> None:
        # (session_id, scenario) -> attempt_count
        self._attempts: Dict[Tuple[str, str], int] = defaultdict(int)
        self._events: List[RecoveryEvent] = []

    def can_attempt(self, session_id: str, scenario: FailureScenario) -> bool:
        recipe = get_recipe(scenario)
        key = (session_id, scenario.value)
        return self._attempts[key] < recipe.max_attempts

    def record_attempt(
        self,
        session_id: str,
        scenario: FailureScenario,
        recovered: bool = False,
    ) -> Optional[RecoveryEvent]:
        key = (session_id, scenario.value)
        self._attempts[key] += 1
        attempt_num = self._attempts[key]

        recipe = get_recipe(scenario)
        step_idx = min(attempt_num - 1, len(recipe.steps) - 1)
        instruction = recipe.steps[step_idx].instruction if recipe.steps else ""

        event = RecoveryEvent(
            session_id=session_id,
            scenario=scenario,
            attempt_number=attempt_num,
            step_index=step_idx,
            instruction=instruction,
            recovered=recovered,
        )
        self._events.append(event)
        return event

    def get_instruction(self, session_id: str, scenario: FailureScenario) -> Optional[str]:
        """Get the recovery instruction without recording an attempt."""
        key = (session_id, scenario.value)
        recipe = get_recipe(scenario)
        attempt_num = self._attempts[key]
        step_idx = min(attempt_num, len(recipe.steps) - 1)
        return recipe.steps[step_idx].instruction if recipe.steps else None

    def exhausted(self, session_id: str, scenario: FailureScenario) -> bool:
        return not self.can_attempt(session_id, scenario)

    def session_events(self, session_id: str) -> List[RecoveryEvent]:
        return [e for e in self._events if e.session_id == session_id]

    def reset_session(self, session_id: str) -> None:
        keys_to_del = [k for k in self._attempts if k[0] == session_id]
        for k in keys_to_del:
            del self._attempts[k]
        self._events = [e for e in self._events if e.session_id != session_id]


# Module-level singleton ledger
recovery_ledger = RecoveryLedger()


def can_attempt(session_id: str, scenario: FailureScenario) -> bool:
    return recovery_ledger.can_attempt(session_id, scenario)


def record_attempt(
    session_id: str,
    scenario: FailureScenario,
    recovered: bool = False,
) -> Optional[RecoveryEvent]:
    return recovery_ledger.record_attempt(session_id, scenario, recovered)
