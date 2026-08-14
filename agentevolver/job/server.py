"""JobManager — the one registry every kind of background work reports to.

Kind-agnostic on purpose. A background shell command, a PTY send, and a spawned agent
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
from typing import Callable, Dict, List, Optional

from agentevolver.job.types import Job, JobStatus
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


class JobManagerServer(metaclass=Singleton):
    """Starts, tracks, reads, and kills background work."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}

    # ------------------------------------------------------------------
    # Starting
    # ------------------------------------------------------------------

    def register(self, *, kind: str, label: str, session_id: str = "",
                 handle: object = None) -> Job:
        """Take a already-started piece of work into the registry.

        Registration is separate from starting because the producers differ so much: a
        `Popen` is running the instant it is constructed, a task starts when the loop
        gets to it. Asking each producer to start its own work and then hand it over
        keeps this class from having to know how any of them begin.
        """
        job = Job(id=f"job_{uuid.uuid4().hex[:8]}", kind=kind, label=label,
                  session_id=session_id, handle=handle)
        self._jobs[job.id] = job
        self._evict(session_id)
        logger.info(f"| 🧵 Job {job.id} started ({kind}): {label[:80]}")
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
                try:
                    os.killpg(os.getpgid(handle.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    handle.terminate()
            elif isinstance(handle, asyncio.Task):
                handle.cancel()
        except Exception as error:                                  # noqa: BLE001
            logger.warning(f"| ⚠️ Could not signal job {job_id}: {error}")

        job.ended_at = time.monotonic()
        job.status = JobStatus.KILLED
        logger.info(f"| 🧵 Job {job_id} killed after {job.elapsed:.1f}s")
        return True

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
