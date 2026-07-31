"""A handler that returns must not be assumed to have succeeded.

TaskManager marked a task DONE whenever the handler did not raise. An agent run
that is force-stopped does not raise — it returns a Response with success=False —
so a ProgramBench run that the no-progress guard ended at step 8, having written no
source file at all, was recorded as `finished as done` and its empty submission was
scored like a real attempt.

`_execute` is driven directly rather than through submit()/start(): TaskManager is a
Singleton with an asyncio worker pool, and standing one up per test does not tear
down cleanly inside pytest.
"""
import pytest

from agentevolver.response.types import Response, ResponseType
from agentevolver.task import TaskCategory, TaskPriority, TaskStatus
from agentevolver.task.server import TaskManager, TaskRecord
from agentevolver.task.types import Task


def _manager(tmp_path, handler):
    manager = TaskManager()
    manager._records = {}
    manager._running_evolver = {}
    manager._log_root = str(tmp_path)
    manager._persist_path = str(tmp_path / "tasks.json")
    manager._archive_path = str(tmp_path / "archive.json")
    manager.set_handler(handler)
    return manager


def _record(manager, content="t"):
    record = TaskRecord(
        task=Task(id="task-1", content=content, priority=TaskPriority.HIGH),
        category=TaskCategory.USER,
    )
    manager._records["task-1"] = record
    return record


@pytest.mark.asyncio
async def test_unsuccessful_response_marks_the_task_failed(tmp_path):
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
async def test_successful_response_still_marks_done(tmp_path):
    async def handler(record):
        return Response(type=ResponseType.AGENT, success=True, message="ok")

    manager = _manager(tmp_path, handler)
    record = _record(manager)
    await manager._execute("task-1")
    assert record.task.status is TaskStatus.DONE
    assert record.error is None


@pytest.mark.asyncio
async def test_non_response_return_value_keeps_the_old_behaviour(tmp_path):
    """Handlers returning None or a plain value are unaffected."""
    async def handler(record):
        return None

    manager = _manager(tmp_path, handler)
    record = _record(manager)
    await manager._execute("task-1")
    assert record.task.status is TaskStatus.DONE


@pytest.mark.asyncio
async def test_raising_handler_still_fails(tmp_path):
    async def handler(record):
        raise RuntimeError("boom")

    manager = _manager(tmp_path, handler)
    record = _record(manager)
    await manager._execute("task-1")
    assert record.task.status is TaskStatus.FAILED
    assert "boom" in (record.error or "")
