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

import asyncio
import json
from typing import Any, Dict, List, Literal, Optional

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
        owner = self._session(ctx)
        if job is not None and (not owner or job.session_id == owner):
            return job, None
        known = [j.id for j in job_manager.list(owner)]
        return None, _fail(
            f"No job {job_id!r}. This session has: {', '.join(known) if known else '(none)'}"
        )

    @staticmethod
    def _record_subscriber_collection(
        job_id: str,
        ctx,
        *,
        full: bool,
        turn: Optional[int] = None,
    ) -> int:
        """A full read acknowledges the latest finished subscription turn."""
        if not full:
            return 0
        extra = getattr(ctx, "extra", None) or {}
        contract = extra.get("website_runtime_contract")
        if not isinstance(contract, dict):
            return 0
        subscriber_ids = {str(item) for item in contract.get("subscriber_job_ids") or []}
        if job_id not in subscriber_ids:
            return 0

        from agentevolver.runtime import runtime_manager

        ref = runtime_manager.child(job_id)
        if ref is None or not ref.alive or ref.turns < 1:
            return 0
        completed_turn = int(turn or ref.turns)
        if completed_turn < 1 or completed_turn > ref.turns:
            return 0
        if turn is None and (ref.busy or not ref._tasks.empty()):
            return 0
        collected = contract.setdefault("collected_turns", {})
        collected[job_id] = max(
            int(collected.get(job_id) or 0),
            completed_turn,
        )
        return int(collected[job_id])

    # ------------------------------------------------------------------ actions
    @environment_manager.action(
        name="list",
        read_only=True,
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
        return _ok(
            f"{len(jobs)} job(s), {running} still running:\n{body}",
            jobs=[j.model_dump(exclude={"handle", "output"}) for j in jobs],
        )

    @environment_manager.action(
        name="output",
        read_only=True,
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
    async def output(
        self,
        job_id: str,
        tail: Optional[int] = None,
        turn: Optional[int] = None,
        ctx=None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        job, failure = self._resolve(job_id, ctx)
        if failure:
            return failure

        from agentevolver.runtime import runtime_manager

        ref = runtime_manager.child(job_id)
        if turn is not None:
            if tail is not None:
                return _fail("turn and tail are mutually exclusive")
            if turn < 1:
                return _fail("turn must be a positive integer")
            if ref is None or turn not in ref.turn_results:
                available = sorted((getattr(ref, "turn_results", None) or {}).keys())
                return _fail(
                    f"No completed turn {turn} for {job_id}; available: {available or '(none)'}"
                )
            text = ref.turn_results[turn]
            diagnostics = (getattr(ref, "turn_diagnostics", None) or {}).get(turn)
            if diagnostics:
                text = (
                    f"{text}\n\n[runtime diagnostics]\n"
                    f"{json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)}"
                )
        else:
            text = job_manager.output(job_id, tail=tail) or ""
        collected_turn = self._record_subscriber_collection(
            job_id,
            ctx,
            full=tail is None,
            turn=turn,
        )
        header = f"{job.id} — {job.status.value}"
        if job.status.is_final and job.exit_code is not None:
            header += f" (exit {job.exit_code})"
        header += f", {job.elapsed:.1f}s"
        idle_after_turn = bool(
            ref and ref.alive and ref.continuable and not ref.busy and ref._tasks.empty()
        )
        if idle_after_turn:
            header += f" — IDLE AFTER TURN {ref.turns}, ready for a later event"
        elif not job.status.is_final:
            # Say it explicitly. Output that simply stops looks the same as a job that
            # finished quietly, and an agent that reads it as finished stops collecting.
            header += " — STILL RUNNING, call again for more"
        if job.truncated:
            header += " (earlier output dropped; the cap keeps the tail)"
        if job.error:
            header += f"\nerror: {job.error}"
        return _ok(
            f"{header}\n\n{text}" if text else f"{header}\n\n(no output yet)",
            job_id=job_id,
            status=job.status.value,
            exit_code=job.exit_code,
            collected_turn=collected_turn or None,
            requested_turn=turn,
            idle_after_turn=idle_after_turn,
            # Stated as data as well as prose: a caller deciding whether to come
            # back should not have to parse a header for it.
            running=not job.status.is_final,
        )

    @environment_manager.action(
        name="wait",
        read_only=True,
        description=(
            "Wait efficiently for background work without spending model turns polling. "
            "For long-lived sub-agents use condition='idle_after_turn' and set min_turns "
            "to the release turn each must have completed. The call returns early if all "
            "targets are ready, any target ends unexpectedly, or timeout expires."
        ),
    )
    async def wait(
        self,
        job_ids: List[str],
        condition: Literal["idle_after_turn", "finished"] = "idle_after_turn",
        min_turns: int = 1,
        timeout: float = 600.0,
        ctx=None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Block one tool call on job state; never generate periodic model turns."""
        from agentevolver.runtime import runtime_manager

        ids = list(dict.fromkeys(str(item) for item in job_ids if str(item).strip()))
        if not ids or len(ids) > STATE_JOB_LIMIT:
            return _fail(f"job_ids must contain 1–{STATE_JOB_LIMIT} unique job ids")
        if condition not in {"idle_after_turn", "finished"}:
            return _fail("condition must be 'idle_after_turn' or 'finished'")
        if min_turns < 0:
            return _fail("min_turns must be non-negative")
        if timeout <= 0 or timeout > 3600:
            return _fail("timeout must be greater than 0 and at most 3600 seconds")

        owner = self._session(ctx)
        resolved = []
        for job_id in ids:
            job = job_manager.get(job_id)
            if job is None or (owner and job.session_id != owner):
                return _fail(f"No background job {job_id!r} in this session")
            resolved.append(job)

        def snapshots() -> tuple[List[Dict[str, Any]], bool, bool]:
            rows: List[Dict[str, Any]] = []
            all_ready = True
            terminal_failure = False
            for job in resolved:
                ref = runtime_manager.child(job.id)
                row: Dict[str, Any] = {
                    "job_id": job.id,
                    "status": job.status.value,
                    "ready": False,
                    "turns": int(getattr(ref, "turns", 0) or 0),
                    "alive": bool(ref and ref.alive),
                    "busy": bool(ref and ref.busy),
                    "queued": int(ref._tasks.qsize()) if ref is not None else 0,
                }
                if condition == "finished":
                    row["ready"] = job.status.is_final
                elif ref is None or not ref.continuable:
                    row["error"] = "idle_after_turn requires a live continuable sub-agent"
                    terminal_failure = True
                elif not ref.alive or job.status.is_final:
                    row["error"] = job.error or "sub-agent ended before reaching the requested turn"
                    terminal_failure = True
                else:
                    row["ready"] = ref.turns >= min_turns and not ref.busy and ref._tasks.empty()
                all_ready = all_ready and bool(row["ready"])
                rows.append(row)
            return rows, all_ready, terminal_failure

        started = asyncio.get_running_loop().time()
        while True:
            rows, ready, failed = snapshots()
            elapsed = asyncio.get_running_loop().time() - started
            if ready:
                return _ok(
                    f"All {len(rows)} job(s) reached {condition} after {elapsed:.1f}s.",
                    condition=condition,
                    min_turns=min_turns,
                    timed_out=False,
                    jobs=rows,
                )
            if failed:
                return _fail(
                    f"A job ended before all targets reached {condition}.",
                    condition=condition,
                    min_turns=min_turns,
                    timed_out=False,
                    jobs=rows,
                )
            remaining = timeout - elapsed
            if remaining <= 0:
                return _fail(
                    f"Timed out after {timeout:.1f}s waiting for {condition}.",
                    condition=condition,
                    min_turns=min_turns,
                    timed_out=True,
                    jobs=rows,
                )
            await asyncio.sleep(min(0.2, remaining))

    @environment_manager.action(
        name="kill",
        destructive=True,
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
            return _ok(
                f"{job_id} had already {job.status.value} after {job.elapsed:.1f}s; "
                f"nothing to stop. Its output is still readable with job__output.",
                job_id=job_id,
                status=job.status.value,
            )

        was_reminder = job.is_reminder and not job.deliveries
        killed = job_manager.kill(job_id)
        logger.info(f"| 🧵 job__kill stopped {job_id}: {killed}")
        if was_reminder:
            # A reminder printed nothing, so "output before the kill is kept" would be an
            # offer of nothing. What the agent needs to know is that it will not fire.
            return _ok(
                f"Cancelled {job_id}. It will not come due: {job.label[:80]}",
                job_id=job_id,
                status=job.status.value,
            )
        return _ok(
            f"Stopped {job_id} after {job.elapsed:.1f}s. Output printed before the "
            f"kill is kept — read it with job__output.",
            job_id=job_id,
            status=job.status.value,
        )

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
        except Exception as error:  # noqa: BLE001
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
