"""JobManager — the one registry every kind of background work reports to.

Type-agnostic on purpose. A background shell command, a PTY send, and a spawned agent
raise exactly three questions — is it done, what did it say, stop it — so they share one
controller and one set of tools. Modelling them separately would hand the agent three
vocabularies for one idea and guarantee that two of them lag behind the third.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from agentevolver.job.types import (
    Job,
    JobStatus,
    ScheduleError,
    next_occurrence,
    resolve_due,
)
from agentevolver.logger import logger
from agentevolver.utils import Singleton

#: Per-job output cap. Beyond this the *head* is dropped: a command's closing lines are
#: almost always the ones that say what happened, and a tail that scrolls away takes the
#: verdict with it.
MAX_OUTPUT_CHARS = 200_000

#: Finished jobs kept per session. A collected job is still worth holding — an agent
#: often reads a result, acts, and comes back — but not forever, and a run that starts
#: hundreds would otherwise keep every byte of every one.
MAX_FINISHED_PER_SESSION = 50


#: How long a kill waits for the process to actually go, at each escalation. Short: this
#: runs on the event loop, and a job that needs longer than this to die after SIGKILL is
#: not going to die at all.
KILL_GRACE_SECONDS = 2.0


def _reap(handle, timeout: float) -> bool:
    """Wait for a signalled process to exit. True when it did.

    `Popen.wait` also reaps the zombie, which matters for a long-lived host: a session
    that kills its jobs and never waits leaves one defunct entry per job in the process
    table for the life of the gateway.
    """
    try:
        handle.wait(timeout=timeout)
        return True
    except Exception:                                               # noqa: BLE001
        return False


class JobManagerServer(metaclass=Singleton):
    """Starts, tracks, reads, and kills background work — including work not started yet."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        #: Wall clock, read through an attribute so a test can pin it. Every
        #: comparison against ``due_at`` goes through here; a test that proved
        #: scheduling by sleeping would prove it slowly and flake under load.
        self.clock: Callable[[], float] = time.time

    # ------------------------------------------------------------------
    # Starting
    # ------------------------------------------------------------------

    def register(self, *, type: str, label: str, session_id: str = "",
                 handle: object = None) -> Job:
        """Take a already-started piece of work into the registry.

        Registration is separate from starting because the producers differ so much: a
        `Popen` is running the instant it is constructed, a task starts when the loop
        gets to it. Asking each producer to start its own work and then hand it over
        keeps this class from having to know how any of them begin.
        """
        job = Job(id=f"job_{uuid.uuid4().hex[:8]}", type=type, label=label,
                  session_id=session_id, handle=handle)
        self._jobs[job.id] = job
        self._evict(session_id)
        logger.info(f"| 🧵 Job {job.id} started ({type}): {label[:80]}")
        return job

    def append_output(self, job_id: str, text: str) -> None:
        """Accumulate output, dropping the head once the cap is passed."""
        job = self._jobs.get(job_id)
        if job is None or not text:
            return
        job.output += text
        if len(job.output) > MAX_OUTPUT_CHARS:
            job.output = job.output[-MAX_OUTPUT_CHARS:]
            job.truncated = True

    def finish(self, job_id: str, *, exit_code: Optional[int] = None,
               error: Optional[str] = None) -> None:
        """Record the end of a job that is already over.

        A job already in a final state is left alone: the first verdict is the true one.
        Without that, a kill followed by the process's own exit would overwrite "killed"
        with "exited", and the agent would read a job it stopped as one that ran to
        completion.
        """
        job = self._jobs.get(job_id)
        if job is None or job.status.is_final:
            return
        job.ended_at = time.monotonic()
        job.exit_code = exit_code
        job.error = error
        job.status = (JobStatus.FAILED if error is not None
                      else JobStatus.EXITED if exit_code == 0
                      else JobStatus.FAILED if exit_code is not None
                      else JobStatus.EXITED)
        logger.info(f"| 🧵 Job {job_id} {job.status.value} after {job.elapsed:.1f}s")

    # ------------------------------------------------------------------
    # Scheduling — work that has not started yet
    # ------------------------------------------------------------------

    def schedule(self, *, session_id: str, prompt: str,
                 after_seconds: Optional[int] = None,
                 at: Optional[str] = None,
                 every_seconds: Optional[int] = None) -> Job:
        """Register a reminder for later. Raises ``ScheduleError`` on a bad selector.

        Session-local, and that is a property rather than a shortfall: the registry
        lives in this process and dies with it, so a reminder is a way to come back
        to something inside one run, not a way to reach the user tomorrow. Anything
        that must outlive the run belongs in the workspace or in a goal.
        """
        text = (prompt or "").strip()
        if not text:
            raise ScheduleError("A reminder needs something to say; the prompt was empty.")

        now = self.clock()
        due_at, interval = resolve_due(now=now, after_seconds=after_seconds,
                                       at=at, every_seconds=every_seconds)
        job = Job(id=f"job_{uuid.uuid4().hex[:8]}", type="reminder", label=text,
                  session_id=session_id, status=JobStatus.SCHEDULED,
                  due_at=due_at, every_seconds=interval)
        self._jobs[job.id] = job
        self._evict(session_id)
        logger.info(f"| ⏰ Reminder {job.id} due in {due_at - now:.0f}s"
                    f"{f' every {interval}s' if interval else ''}: {text[:80]}")
        return job

    def reminders(self, session_id: str = "") -> List[Job]:
        """Scheduled reminders for one session, soonest first."""
        pending = [j for j in self._jobs.values()
                   if j.is_reminder and j.status is JobStatus.SCHEDULED
                   and (not session_id or j.session_id == session_id)]
        return sorted(pending, key=lambda j: (j.due_at or 0, j.started_at))

    def due(self, session_id: str = "") -> List[Job]:
        """Reminders whose time has come and which nothing has collected yet."""
        now = self.clock()
        return [j for j in self.reminders(session_id) if (j.due_at or 0) <= now]

    def claim_due(self, session_id: str = "") -> List[Job]:
        """Take every due reminder, exactly once, and advance the repeating ones.

        Returns a snapshot of each record as it came due — the caller needs the
        occurrence it is delivering, and the stored record has already moved on.
        Claiming is what makes delivery at-most-once: two callers polling the same
        session must not both announce the same reminder.

        A repeating reminder advances to its next aligned occurrence and skips the
        ones that were missed. Coming back from an hour offline should say "this is
        due", not repeat the last twelve times it was.
        """
        now = self.clock()
        claimed: List[Job] = []
        for job in self.due(session_id):
            occurrence = job.due_at or now
            claimed.append(job.model_copy())
            stamp = datetime.fromtimestamp(occurrence, timezone.utc).isoformat(timespec="seconds")
            self.append_output(job.id, f"[reminder due {stamp}] {job.label}\n")
            job.deliveries += 1
            if job.every_seconds:
                # Stepped from the occurrence just delivered, which is itself
                # aligned to creation, so the alignment survives every advance
                # without keeping a second copy of the anchor to disagree with.
                job.due_at = next_occurrence(anchor=occurrence,
                                             every_seconds=job.every_seconds, now=now)
            else:
                job.due_at = None
                job.ended_at = time.monotonic()
                job.status = JobStatus.EXITED
                job.exit_code = 0
            logger.info(f"| ⏰ Reminder {job.id} delivered ({job.deliveries}): {job.label[:60]}")
        return claimed

    # ------------------------------------------------------------------
    # Collecting
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self, session_id: str = "") -> List[Job]:
        """Jobs for one session, newest first; every job when no session is given."""
        jobs = [j for j in self._jobs.values()
                if not session_id or j.session_id == session_id]
        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    def output(self, job_id: str, *, tail: Optional[int] = None) -> Optional[str]:
        """What the job has said so far. Repeatable — reading does not consume.

        An agent that polls has to see the earlier output on every read; a consuming read
        makes "nothing new" and "I already took it" the same answer.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        text = job.output
        if tail is not None and tail > 0:
            lines = text.splitlines()
            if len(lines) > tail:
                text = "\n".join(lines[-tail:])
        return text

    # ------------------------------------------------------------------
    # Stopping
    # ------------------------------------------------------------------

    def kill(self, job_id: str) -> bool:
        """Stop a running job. Idempotent; already-finished jobs report False.

        The whole process group is signalled, not just the leader. A shell command is
        usually a shell that spawned the real work, and killing only the shell leaves the
        work running while the registry reports it dead — the worst of both.
        """
        job = self._jobs.get(job_id)
        if job is None or job.status.is_final:
            return False

        handle = job.handle
        try:
            if hasattr(handle, "pid") and handle.pid:
                self._stop_process(job_id, handle)
            elif isinstance(handle, asyncio.Task):
                handle.cancel()
        except Exception as error:                                  # noqa: BLE001
            logger.warning(f"| ⚠️ Could not signal job {job_id}: {error}")

        job.ended_at = time.monotonic()
        job.status = JobStatus.KILLED
        logger.info(f"| 🧵 Job {job_id} killed after {job.elapsed:.1f}s")
        return True

    @staticmethod
    def _stop_process(job_id: str, handle) -> None:
        """Signal the group and wait for it to actually stop.

        Sending SIGTERM and returning is a request, not an ending. A process that traps
        the signal — or a shell whose child ignores it — keeps running while the registry
        reports it killed, which is the same lie the group-signal above exists to prevent,
        arriving through a different door.

        So: term the group, wait briefly, and escalate to SIGKILL for whatever is still
        there. SIGKILL cannot be trapped, so the second wait is the one that settles it.
        """
        pid = handle.pid
        try:
            group = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            handle.terminate()
            _reap(handle, KILL_GRACE_SECONDS)
            return

        os.killpg(group, signal.SIGTERM)
        if _reap(handle, KILL_GRACE_SECONDS):
            return

        logger.warning(f"| ⚠️ Job {job_id} ignored SIGTERM; sending SIGKILL")
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            handle.kill()
        if not _reap(handle, KILL_GRACE_SECONDS):
            # Uninterruptible sleep — a wedged mount, usually. Nothing more to send, and
            # saying so beats a log that implies the process stopped.
            logger.warning(f"| ⚠️ Job {job_id} did not exit after SIGKILL; it is unkillable "
                           f"from here (pid {pid})")

    def forget(self, session_id: str) -> None:
        """Drop a finished session's jobs. Running ones are killed first."""
        for job in [j for j in self._jobs.values() if j.session_id == session_id]:
            if not job.status.is_final:
                self.kill(job.id)
            self._jobs.pop(job.id, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict(self, session_id: str) -> None:
        """Hold the finished-job count for one session.

        Only finished jobs are dropped, and oldest first. A running job is never evicted
        no matter how many there are: forgetting it would orphan a live process that
        nothing can then report on or stop.
        """
        finished = [j for j in self._jobs.values()
                    if j.session_id == session_id and j.status.is_final]
        for job in sorted(finished, key=lambda j: j.ended_at or 0)[:-MAX_FINISHED_PER_SESSION]:
            self._jobs.pop(job.id, None)


job_manager = JobManagerServer()

__all__ = ["job_manager", "JobManagerServer", "MAX_OUTPUT_CHARS"]
