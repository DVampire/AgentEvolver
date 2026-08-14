"""A handler that returns must not be assumed to have succeeded.

TaskManager marked a task DONE whenever the handler did not raise. An agent run that is
force-stopped does not raise — it returns a Response with success=False — so a
ProgramBench run that the no-progress guard ended at step 8, having written no source file
at all, was recorded as `finished as done` and its empty submission was scored like a real
attempt. The status is what every reader downstream trusts: the archive, the stats
summary, and any dependent task waiting on DONE.

The rule the code settled on is narrow, and the tests below pin both of its edges: only an
explicit `success=False` downgrades a task, so handlers that return None or a plain value
keep the behaviour they always had.

`_execute` is driven directly rather than through submit()/start(): TaskManager is a
Singleton with an asyncio worker pool, and standing one up per test does not tear down
cleanly inside pytest.
"""

import pytest

from agentevolver.response.types import Response, ResponseType
from agentevolver.task import TaskCategory, TaskPriority, TaskStatus
from agentevolver.task.server import TaskManager, TaskRecord
from agentevolver.task.types import Task


def _manager(tmp_path, handler):
    """A TaskManager with its state reset and its persistence pointed at tmp_path.

    TaskManager is a Singleton, so this is the same object every test gets; the fields are
    reassigned rather than constructed fresh so one test's records cannot reach the next.
    No workers are started — `_execute` is called directly.
    """
    manager = TaskManager()
    manager._records = {}
    manager._running_evolver = {}
    manager._log_root = str(tmp_path)
    manager._persist_path = str(tmp_path / "tasks.json")
    manager._archive_path = str(tmp_path / "archive.json")
    manager.set_handler(handler)
    return manager


def _record(manager, content="t"):
    """Register one PENDING user task for `_execute` to pick up by id."""
    record = TaskRecord(
        task=Task(id="task-1", content=content, priority=TaskPriority.HIGH),
        category=TaskCategory.USER,
    )
    manager._records["task-1"] = record
    return record


@pytest.mark.asyncio
async def test_a_handler_that_returns_a_failure_response_has_not_done_the_work(tmp_path):
    """This is the case the old code read as success, because nothing was raised.

    The message is the real one from the no-progress guard: an agent stopped early returns
    normally, carrying its failure in the Response rather than in an exception. Recording
    it as DONE is worse than losing the task, because the empty result then travels as a
    finished attempt — it gets archived, scored, and unblocks whatever depended on it.
    The error text is checked too: a FAILED task with no reason leaves the next reader
    guessing what stopped it.
    """
    async def handler(record):
        return Response(
            type=ResponseType.AGENT, success=False,
            message="Stopped after three no-progress action proposals.",
        )

    manager = _manager(tmp_path, handler)
    record = _record(manager)
    await manager._execute("task-1")
    assert record.task.status is TaskStatus.FAILED
    assert "no-progress" in (record.error or "").lower()


@pytest.mark.asyncio
async def test_a_successful_response_still_finishes_as_done(tmp_path):
    """The ordinary path, which the new check must not have made stricter.

    A rule that inspected the Response too eagerly — treating a missing field or a falsy
    payload as failure — would fail working tasks, and that is the more expensive
    direction: real work gets retried or abandoned. `error` staying None says nothing was
    recorded against a task that went fine.
    """
    async def handler(record):
        return Response(type=ResponseType.AGENT, success=True, message="ok")

    manager = _manager(tmp_path, handler)
    record = _record(manager)
    await manager._execute("task-1")
    assert record.task.status is TaskStatus.DONE
    assert record.error is None


@pytest.mark.asyncio
async def test_a_handler_that_returns_no_response_at_all_is_still_done(tmp_path):
    """Only an explicit success=False downgrades; absence of a verdict is not a verdict.

    Most handlers in the codebase return None or a plain value. Treating "no Response" as
    "not successful" would fail every one of them at once, which is why the check is on
    `isinstance(result, Response) and result.success is False` rather than on the
    truthiness of whatever came back.
    """
    async def handler(record):
        return None

    manager = _manager(tmp_path, handler)
    record = _record(manager)
    await manager._execute("task-1")
    assert record.task.status is TaskStatus.DONE


@pytest.mark.asyncio
async def test_a_handler_that_raises_still_fails_the_task(tmp_path):
    """The path that already worked, kept honest while a second failure path was added.

    The exception reaches a different branch of `_execute` than the Response check does,
    and that branch also owns retries — this record leaves `max_retries` at its default of
    0, so the first exception is terminal rather than re-queued. The exception text has to
    survive into `record.error` for the same reason as above: a failure nobody can read is
    a failure nobody can act on.
    """
    async def handler(record):
        raise RuntimeError("boom")

    manager = _manager(tmp_path, handler)
    record = _record(manager)
    await manager._execute("task-1")
    assert record.task.status is TaskStatus.FAILED
    assert "boom" in (record.error or "")
