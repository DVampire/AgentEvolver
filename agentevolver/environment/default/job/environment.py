"""Background work as somewhere the agent is, rather than three tools it must remember.

A backgrounded shell command, a backgrounded terminal send, a dispatched sub-agent and a
reminder raise the same three questions — is it done, what did it say, stop it — so one
registry answers all four kinds. That part is `agentevolver.job` and is unchanged.

What changed is how the agent learns any of it. As tools, the answer to "what am I still
waiting on" arrived only when the agent thought to ask, and background work is exactly the
kind that is easy to forget: it makes no noise, and a job that finished, failed or hung
looks identical from the outside — like nothing at all.

The same lesson was already learned here for half of it. `_deliver_due_reminders` says: *a
reminder the agent has to remember to look for is not a reminder — `job_list_tool` could
show them, but nothing pushed, so the agent saw a due reminder only if it happened to list
its jobs, which is precisely the thing it set the reminder in order not to have to do.*
Reminders got a push; running jobs did not. Now `get_state` renders both, every step.

`list` stays as an action, for the same reason `terminal__read` stayed: the state is the
live tail, and there is still a use for asking about the whole of it — finished jobs
included — deliberately.
"""

from typing import Any, Dict, Optional

from pydantic import ConfigDict, Field

from agentevolver.environment.server import environment_manager
from agentevolver.environment.types import Environment
from agentevolver.job import job_manager
from agentevolver.logger import logger
from agentevolver.registry import ENVIRONMENT

#: How many jobs the state will name before it stops. A session with fifty finished jobs
#: should not spend the prompt on them every step; running work is what has to be visible,
#: and `list` is the way to see everything.
STATE_JOB_LIMIT = 12


def _fail(message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": False, "message": message, **extra}


def _ok(message: str, **extra: Any) -> Dict[str, Any]:
    return {"success": True, "message": message, **extra}


@ENVIRONMENT.register_module(force=True)
class JobEnvironment(Environment):
    """Everything this session started in the background, and the actions that control it."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="job")
    description: str = Field(
        default="Background work this session started — a backgrounded command, a "
                "backgrounded terminal send, a dispatched sub-agent, a reminder. What is "
                "still running is shown every step; the actions collect output and stop "
                "things."
    )
    metadata: Dict[str, Any] = Field(default={"has_vision": False})
    enable_evolving: bool = Field(default=False)

    @staticmethod
    def _session(ctx) -> str:
        return str(getattr(ctx, "id", "") or "")

    def _resolve(self, job_id: str, ctx):
        """The job, or a failure naming the ids that exist.

        A bare "not found" leaves the agent guessing at a handle it is holding — usually
        one it mistyped, or one from a session that has since been forgotten.
        """
        job = job_manager.get(job_id)
        if job is not None:
            return job, None
        known = [j.id for j in job_manager.list(self._session(ctx))]
        return None, _fail(f"No job {job_id!r}. This session has: "
                           f"{', '.join(known) if known else '(none)'}")

    # ------------------------------------------------------------------ actions
    @environment_manager.action(
        name="list",
        description=(
            "Every background job this session started, newest first, with its state and "
            "how long it has been running.\n\n"
            "What is still running already arrives in `environment-state` each step, so "
            "reach for this when you want what that does not carry: jobs that have already "
            "finished, and the full listing when the state has been trimmed.\n\n"
            "Elapsed time is the signal that separates working from hung — a job that has "
            "printed nothing for minutes is telling you something its status alone does not."
        ),
    )
    async def list(self, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        jobs = job_manager.list(self._session(ctx))
        if not jobs:
            # Not an error, and worth saying plainly: an empty listing after starting
            # something is a real signal, and "no output" would read as a broken action.
            return _ok("No background jobs in this session.")
        running = sum(1 for j in jobs if not j.status.is_final)
        # One clock for the listing and for the registry's own due-ness decisions:
        # rendering "in 30s" from a different clock than the one that fires the reminder is
        # how a listing shows work as pending after it has already fired.
        now = job_manager.clock()
        body = "\n".join(j.summary(now) for j in jobs)
        return _ok(f"{len(jobs)} job(s), {running} still running:\n{body}",
                   jobs=[j.model_dump(exclude={"handle", "output"}) for j in jobs])

    @environment_manager.action(
        name="output",
        description=(
            "Collect a background job's output.\n\n"
            "Safe to call repeatedly — reading does not consume, so an early check still "
            "shows everything when you come back. A finished job keeps its output; a job "
            "that failed is exactly when you want it.\n\n"
            "If the job is still running you get what it has printed up to now, not a "
            "wait; call it again later for more. `tail` returns only the last N lines — "
            "use it on a chatty job, where the closing lines are almost always the ones "
            "that say what happened."
        ),
    )
    async def output(self, job_id: str, tail: Optional[int] = None,
                     ctx=None, **kwargs: Any) -> Dict[str, Any]:
        job, failure = self._resolve(job_id, ctx)
        if failure:
            return failure

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
        return _ok(f"{header}\n\n{text}" if text else f"{header}\n\n(no output yet)",
                   job_id=job_id, status=job.status.value, exit_code=job.exit_code,
                   # Stated as data as well as prose: a caller deciding whether to come
                   # back should not have to parse a header for it.
                   running=not job.status.is_final)

    @environment_manager.action(
        name="kill",
        description=(
            "Stop a background job you no longer need, or one that is not going to "
            "finish.\n\n"
            "The whole process tree is signalled, not just the command that was typed — a "
            "shell command is usually a shell that started the real work. Output printed "
            "before the kill is kept, so this does not destroy what you already learned.\n\n"
            "For a job watching a terminal, this stops the *watching*; the command keeps "
            "running, and `terminal__signal` is what stops that."
        ),
    )
    async def kill(self, job_id: str, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        job, failure = self._resolve(job_id, ctx)
        if failure:
            return failure
        if job.status.is_final:
            # Already over is not a failure — the agent's intent is satisfied either way,
            # and reporting it as an error invites a retry loop against a dead process.
            return _ok(f"{job_id} had already {job.status.value} after {job.elapsed:.1f}s; "
                       f"nothing to stop. Its output is still readable with job__output.",
                       job_id=job_id, status=job.status.value)

        was_reminder = job.is_reminder and not job.deliveries
        killed = job_manager.kill(job_id)
        logger.info(f"| 🧵 job__kill stopped {job_id}: {killed}")
        if was_reminder:
            # A reminder printed nothing, so "output before the kill is kept" would be an
            # offer of nothing. What the agent needs to know is that it will not fire.
            return _ok(f"Cancelled {job_id}. It will not come due: {job.label[:80]}",
                       job_id=job_id, status=job.status.value)
        return _ok(f"Stopped {job_id} after {job.elapsed:.1f}s. Output printed before the "
                   f"kill is kept — read it with job__output.",
                   job_id=job_id, status=job.status.value)

    # ------------------------------------------------------------------ state
    async def get_state(self, ctx=None, **kwargs: Any) -> Dict[str, Any]:
        """What is still outstanding, every step, without being asked.

        The reason this is an environment. Background work is silent by construction: a
        job that finished, one that failed and one that hung all look the same from
        outside — like nothing at all — so the agent that started it has to remember, and
        remembering is what it delegated the work to avoid.

        Only unfinished work is rendered. A finished job has said everything it is going
        to; its output stays readable through `output`, and its line here every step would
        be prompt spent on something that is over. `list` is where the whole history is.
        """
        session = self._session(ctx)
        try:
            jobs = [j for j in job_manager.list(session) if not j.status.is_final]
        except Exception as error:                                   # noqa: BLE001
            logger.warning(f"| ⚠️ could not read job state: {error}")
            return {"success": True, "state": f"[job state unavailable — {error}]"}
        if not jobs:
            return {"success": True, "state": ""}

        now = job_manager.clock()
        shown = jobs[:STATE_JOB_LIMIT]
        lines = [j.summary(now) for j in shown]
        if len(jobs) > len(shown):
            lines.append(f"... and {len(jobs) - len(shown)} more — job__list for all of them")
        return {"success": True, "state": "\n".join(lines)}


__all__ = ["JobEnvironment"]
