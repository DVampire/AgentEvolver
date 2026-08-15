"""What running a model-written program produces, and how it reaches the tools.

Two vocabularies live here and they are deliberately separate. :class:`CodeRunResult`
belongs to the substrate: a program ran, it printed some things, it ended with a value
or with a failure. :class:`GuardedDispatch` belongs to the caller: it is the single
callable through which a program reaches anything outside itself, and the only reason
it is a named type is that both the agent (which builds it) and the tool (which turns
it into bindings) have to agree on what it is.

The failure kinds are separate for the same reason ``JobStatus.KILLED`` is not
``FAILED``: a program that was cut off at its time budget did not raise, and a program
that raised did not run out of time. Collapsing them would tell the agent to fix the
wrong thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

#: The tool that carries a program, named here because two modules have to agree on it:
#: the tool declares it, and the agent recognises it as the one dispatch that is handed a
#: way to dispatch further actions. Written once so a rename cannot half-happen.
RUN_CODE_TOOL = "run_code_tool"

#: Ceiling on what one program's captured output contributes to the result, in
#: characters. The tool pipeline already bounds a whole result (``OUTPUT_LIMIT``), but
#: it bounds it *after* the fact — this keeps a program that prints in a loop from
#: accumulating megabytes in the host before anything gets to trim it.
MAX_LOG_CHARS = 24_000


class CodeFailureType(str, Enum):
    """Why a program did not produce a value.

    Kept apart rather than folded into one error string because the agent's next move
    differs by type: an exception is a bug in the program it just wrote, a timeout is a
    program that was too slow or waited on something that never came, and a runtime exit
    is the substrate dying underneath it — which is not the program's fault at all and
    should not send the model off rewriting working code.
    """

    EXCEPTION = "exception"
    TIMEOUT = "timeout"
    RUNTIME_EXIT = "runtime_exit"


class CodeFailure(BaseModel):
    """One reason a run ended without a value."""

    type: CodeFailureType = Field(description="Which way the run ended.")
    message: str = Field(default="", description="What to show the model, already formatted.")


class CodeRunResult(BaseModel):
    """Everything one program produced: what it printed, what it returned, how it ended."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    value: Any = Field(default=None, description="What the program returned, if anything.")
    logs: List[str] = Field(default_factory=list, description="Lines the program printed, in order.")
    failure: Optional[CodeFailure] = Field(default=None, description="Set when the program did not complete.")
    calls: int = Field(default=0, description="How many tool calls the program made.")

    @property
    def success(self) -> bool:
        return self.failure is None

    def as_message(self) -> str:
        """Render the run for the model.

        Logs come first and are kept even when the run failed: a program that printed
        four filenames and then raised on the fifth has already done most of the work,
        and dropping the output to report the exception alone throws that away.
        """
        parts: List[str] = []
        if self.logs:
            parts.append("\n".join(self.logs))
        if self.failure is not None:
            parts.append(f"[{self.failure.type.value}] {self.failure.message}".rstrip())
        elif self.value is not None:
            parts.append(f"Returned: {self.value}")
        if not parts:
            # A program that printed nothing and returned nothing succeeded at
            # something. Saying so beats an empty result, which reads as a broken tool.
            return "The program ran and produced no output."
        return "\n\n".join(parts)


@dataclass(frozen=True)
class GuardedDispatch:
    """The one way a program reaches a capability: back through the agent's own dispatch.

    ``call(name, args)`` returns the capability's result as text, or raises — it is the
    agent's per-action method with its permission check, its plan-mode gate and its hook
    pairs, not a shortcut to the tool manager. ``names`` is what the program may call;
    anything else is refused before it becomes a dispatch.
    """

    names: Tuple[str, ...]
    call: Callable[[str, Dict[str, Any]], Awaitable[str]]


__all__ = [
    "MAX_LOG_CHARS",
    "RUN_CODE_TOOL",
    "CodeFailure",
    "CodeFailureType",
    "CodeRunResult",
    "GuardedDispatch",
]
