"""Set a reminder for later in this run.

There is one tool here, not three. A reminder is a job that has not started, so the
questions that would need the other two — what is scheduled, cancel that one — are
already answered by `job__list` and `job__kill` against the same registry.
A second list and a second delete would be a second vocabulary for one idea, and the
agent would have to learn which of the two knew about which piece of work.
"""

from typing import Any, Dict, List, Optional

from pydantic import Field

from agentevolver.job import job_manager
from agentevolver.job.types import ScheduleError, format_interval
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_DESCRIPTION = "Set a reminder that comes due later in this run — after a delay, at a time, or at a fixed interval."
_GUIDANCE = """
Park something you must come back to, instead of holding it in your head or polling for
it. Give exactly one of:

- after_seconds — a delay from now, e.g. 900 for "in fifteen minutes".
- at — an absolute RFC 3339 instant with an offset, e.g. "2026-08-15T09:00:00+08:00" or
  "2026-08-15T01:00:00Z". A time without an offset is refused: it names no instant, and
  guessing a time zone is how a reminder fires at breakfast in the wrong hemisphere.
- every_seconds — a fixed interval, at least 300 (five minutes).

- Delivery is session-local. The reminder lives in this run and dies with it — it does
  not reach anyone after the run ends, and it is not a notification. Something that must
  outlive the run belongs in a file, or in the goal.
- Reminders are collected, not pushed. A due one appears in `job__list` as DUE NOW;
  read what it says with `job__output`, and cancel it with `job__kill`.
- A repeating reminder skips what it missed: coming back after an hour gives you one
  reminder, not twelve.
- The prompt is what you will read later — write it so it makes sense with no memory of
  now. "check the deploy" is worse than "check whether the 14:30 deploy of api-gateway
  finished and rolled back cleanly".
"""

_EXAMPLES = [
    '{"name": "schedule_create_tool", "args": {"prompt": "Check whether the nightly ETL finished", "after_seconds": 1800}}',
    '{"name": "schedule_create_tool", "args": {"prompt": "Re-read the build log for new failures", "every_seconds": 600}}',
    '{"name": "schedule_create_tool", "args": {"prompt": "Hand over the summary", "at": "2026-08-15T09:00:00+08:00"}}',
]


def _session_of(kwargs) -> str:
    ctx = kwargs.get("ctx")
    return str(getattr(ctx, "id", "") or "")


@TOOL.register_module(force=True)
class ScheduleCreateTool(Tool):
    """Register a reminder in the job registry."""

    name: str = "schedule_create_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Adds a record to this session's job registry.")
    mutates: Optional[bool] = True

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, prompt: str, after_seconds: Optional[int] = None,
                       at: Optional[str] = None, every_seconds: Optional[int] = None,
                       **kwargs) -> Response:
        """Schedule a prompt to be delivered later, once or repeatedly.

        Args:
            prompt: What to say when it comes due.
            after_seconds: Fire once, this many seconds from now.
            at: Fire once, at this RFC 3339 instant.
            every_seconds: Fire repeatedly, this often; minimum 300.
        """
        try:
            job = job_manager.schedule(
                session_id=_session_of(kwargs), prompt=prompt,
                after_seconds=after_seconds, at=at, every_seconds=every_seconds,
            )
        except ScheduleError as error:
            # The message names what to fix. A bare "invalid arguments" would send the
            # model round the same call with a different guess.
            return Response(type=ResponseType.TOOL, success=False, message=str(error))

        remaining = job.seconds_until_due(job_manager.clock()) or 0
        repeat = f", then every {format_interval(job.every_seconds)}" if job.every_seconds else ""
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"{job.id} is due in {format_interval(remaining)}{repeat}. It shows as "
                     f"DUE NOW in job__list when it fires; job__kill cancels it. "
                     f"Delivery is session-local — it ends when this run does."),
            data={"job_id": job.id, "due_at": job.due_at,
                  "every_seconds": job.every_seconds, "prompt": job.label},
        )
