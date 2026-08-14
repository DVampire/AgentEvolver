"""The three tools that control background work: list, output, kill.

Kind-agnostic by design. A backgrounded shell command, a PTY send, and a spawned agent
raise exactly three questions — is it done, what did it say, stop it — so one set of tools
answers all of them. Giving each producer its own controller would mean three vocabularies
for one idea, and two of them drifting behind the third.
"""

from typing import Any, Dict, Optional

from pydantic import Field

from agentevolver.job import job_manager
from agentevolver.logger import logger
from agentevolver.registry import TOOL
from agentevolver.response.types import Response, ResponseType
from agentevolver.tool.types import Tool

_LIST_DESCRIPTION = "List background jobs and whether each is still running."
_LIST_INSTRUCTION = """
## Function
Show every background job this session started, newest first, with its state and how long
it has been running. Use it to see what is still in flight before deciding whether to
wait, collect a result, or stop something.

Elapsed time is the signal that separates working from hung: a job that has printed
nothing for minutes is telling you something a status alone does not.

## Parameters
(none)

## Example
{"name": "job_list_tool", "args": {}}
"""

_OUTPUT_DESCRIPTION = "Read what a background job has printed so far."
_OUTPUT_INSTRUCTION = """
## Function
Collect a background job's output. Safe to call repeatedly — reading does not consume, so
an early check still shows everything when you come back. A finished job keeps its output;
a job that failed is exactly when you want it.

If the job is still running you get what it has printed up to now, not a wait. Call it
again later for more.

## Parameters
- job_id (str): The id returned when the job was started.
- tail (int, optional): Return only the last N lines. Use it on a chatty job — the closing
  lines are almost always the ones that say what happened.

## Example
{"name": "job_output_tool", "args": {"job_id": "job_1a2b3c4d"}}
{"name": "job_output_tool", "args": {"job_id": "job_1a2b3c4d", "tail": 40}}
"""

_KILL_DESCRIPTION = "Stop a running background job."
_KILL_INSTRUCTION = """
## Function
Stop a background job you no longer need, or one that is not going to finish. The whole
process tree is signalled, not just the command you typed — a shell command is usually a
shell that started the real work.

Output printed before the kill is kept, so this does not destroy what you already learned.

## Parameters
- job_id (str): The id returned when the job was started.

## Example
{"name": "job_kill_tool", "args": {"job_id": "job_1a2b3c4d"}}
"""


def _session_of(kwargs) -> str:
    ctx = kwargs.get("ctx")
    return str(getattr(ctx, "id", "") or "")


@TOOL.register_module(force=True)
class JobListTool(Tool):
    """List this session's background jobs."""

    name: str = "job_list_tool"
    description: str = _LIST_DESCRIPTION
    instruction: str = _LIST_INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads the job registry; changes nothing.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, **kwargs) -> Response:
        jobs = job_manager.list(_session_of(kwargs))
        if not jobs:
            # Not an error, and worth saying plainly: an empty listing after starting
            # something is a real signal, and "no output" would read as a broken tool.
            return Response(type=ResponseType.TOOL, success=True,
                            message="No background jobs in this session.")
        running = sum(1 for j in jobs if not j.status.is_final)
        body = "\n".join(j.summary() for j in jobs)
        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"{len(jobs)} job(s), {running} still running:\n{body}",
            data={"jobs": [j.model_dump(exclude={"handle", "output"}) for j in jobs]},
        )


@TOOL.register_module(force=True)
class JobOutputTool(Tool):
    """Read a background job's accumulated output."""

    name: str = "job_output_tool"
    description: str = _OUTPUT_DESCRIPTION
    instruction: str = _OUTPUT_INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="read_only", description="Reads accumulated output; changes nothing.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, job_id: str, tail: Optional[int] = None, **kwargs) -> Response:
        job = job_manager.get(job_id)
        if job is None:
            known = [j.id for j in job_manager.list(_session_of(kwargs))]
            return Response(
                type=ResponseType.TOOL, success=False,
                message=(f"No job {job_id!r}. This session has: "
                         f"{', '.join(known) if known else '(none)'}"),
            )

        text = job_manager.output(job_id, tail=tail) or ""
        header = f"{job.id} — {job.status.value}"
        if job.status.is_final and job.exit_code is not None:
            header += f" (exit {job.exit_code})"
        header += f", {job.elapsed:.1f}s"
        if not job.status.is_final:
            # Say it explicitly. Output that simply stops looks the same as a job that
            # finished quietly, and an agent that reads it as finished stops collecting.
            header += " — STILL RUNNING, call again for more"
        if job.truncated:
            header += " (earlier output dropped; the cap keeps the tail)"
        if job.error:
            header += f"\nerror: {job.error}"

        return Response(
            type=ResponseType.TOOL, success=True,
            message=f"{header}\n\n{text}" if text else f"{header}\n\n(no output yet)",
            data={"job_id": job.id, "status": job.status.value,
                  "exit_code": job.exit_code, "running": not job.status.is_final},
        )


@TOOL.register_module(force=True)
class JobKillTool(Tool):
    """Stop a running background job."""

    name: str = "job_kill_tool"
    description: str = _KILL_DESCRIPTION
    instruction: str = _KILL_INSTRUCTION
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(default="workspace_write", description="Signals a process this session started.")

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, job_id: str, **kwargs) -> Response:
        job = job_manager.get(job_id)
        if job is None:
            return Response(type=ResponseType.TOOL, success=False,
                            message=f"No job {job_id!r}.")
        if job.status.is_final:
            # Already over is not a failure — the agent's intent is satisfied either way,
            # and reporting it as an error invites a retry loop against a dead process.
            return Response(
                type=ResponseType.TOOL, success=True,
                message=(f"{job_id} had already {job.status.value} after "
                         f"{job.elapsed:.1f}s; nothing to stop. Its output is still "
                         f"readable with job_output_tool."),
            )

        killed = job_manager.kill(job_id)
        logger.info(f"| 🧵 job_kill_tool stopped {job_id}: {killed}")
        return Response(
            type=ResponseType.TOOL, success=True,
            message=(f"Stopped {job_id} after {job.elapsed:.1f}s. Output printed before "
                     f"the kill is kept — read it with job_output_tool."),
            data={"job_id": job_id, "status": job.status.value},
        )
