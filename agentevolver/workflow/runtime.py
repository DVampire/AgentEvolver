"""Background-style runtime for compiled dynamic workflow programs."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from agentevolver.response import Response
from agentevolver.utils import make_id

from .types import (
    ExecutionFrame, ExecutionState, InvocationAttempt, InvocationRun,
    InvocationState, StepType, WorkflowDefinition, WorkflowRun, WorkflowState,
)

_EXPR = re.compile(r"\$\{([^{}]+)\}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowRuntime:
    """Execute safe IR while preserving a fully inspectable execution hierarchy.

    WorkflowStep is static program text. ExecutionFrame records each dynamic expansion
    (including map items and loop rounds). InvocationRun records concrete capability
    calls and their attempts. This separation is what keeps dynamic workflows from
    collapsing back into a fixed DAG model.
    """

    RUNTIME_VERSION = "1.0.0"

    def __init__(self) -> None:
        self._cancel: Dict[str, asyncio.Event] = {}
        self._active: Dict[str, set[asyncio.Task]] = {}
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._continue: Dict[str, asyncio.Event] = {}
        self._runs: Dict[str, WorkflowRun] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    async def run(self, definition: WorkflowDefinition, *, input=None, ctx=None, depth=0, run_id=None) -> WorkflowRun:
        run = WorkflowRun(
            id=run_id or make_id(), workflow_name=definition.name,
            workflow_version=definition.version, runtime_version=self.RUNTIME_VERSION,
            state=WorkflowState.CREATED, input=dict(input or {}), started_at=_now(),
        )
        self._runs[run.id] = run
        run.checkpoint_path = str(self._checkpoint_path(run, ctx))
        run.state = WorkflowState.VALIDATING
        try:
            if depth >= definition.max_depth:
                raise ValueError(f"Workflow nesting exceeds max_depth={definition.max_depth}")
            values = self._validate_inputs(definition, input or {})
        except Exception as exc:
            run.state, run.error, run.finished_at = WorkflowState.REJECTED, str(exc), _now()
            self._write_checkpoint(run)
            self._tasks.pop(run.id, None)
            return run
        run.input, run.variables = values, {"inputs": values}
        run.state = WorkflowState.READY
        self._write_checkpoint(run)
        return await self._drive(definition, run, ctx, depth)

    async def resume(self, definition: WorkflowDefinition, checkpoint: str | Path, *, ctx=None, depth=0) -> WorkflowRun:
        path = Path(checkpoint).resolve()
        run = WorkflowRun.model_validate_json(path.read_text(encoding="utf-8"))
        if run.runtime_version.split(".", 1)[0] != self.RUNTIME_VERSION.split(".", 1)[0]:
            raise ValueError(
                f"Checkpoint runtime_version {run.runtime_version} is incompatible with {self.RUNTIME_VERSION}"
            )
        if (run.workflow_name, run.workflow_version) != (definition.name, definition.version):
            raise ValueError("Checkpoint does not match workflow name and version")
        run.state = WorkflowState.RESUMING
        for item in run.frames.values():
            if item.state in {ExecutionState.RUNNING, ExecutionState.CANCELLED, ExecutionState.FAILED, ExecutionState.RETRY_WAIT}:
                item.state = ExecutionState.PENDING
                item.error = None
        for item in run.invocations.values():
            if item.state == InvocationState.COMPLETED:
                item.state, item.cached = InvocationState.CACHED, True
            elif item.state not in {InvocationState.CACHED}:
                item.state, item.error = InvocationState.QUEUED, None
        run.error, run.finished_at, run.paused_at = None, None, None
        run.checkpoint_path = str(path)
        return await self._drive(definition, run, ctx, depth)

    async def _drive(self, definition, run, ctx, depth):
        cancel = asyncio.Event()
        self._cancel[run.id], self._active[run.id] = cancel, set()
        self._semaphores[run.id] = asyncio.Semaphore(definition.max_concurrency)
        continue_event = asyncio.Event(); continue_event.set()
        self._continue[run.id] = continue_event
        self._runs[run.id] = run
        run.state = WorkflowState.RUNNING
        await self._emit("workflow_start", run, ctx, input=run.input)
        try:
            execution = self._execute_steps(definition.program, definition, run, run.variables, ctx, depth, "root")
            if definition.timeout:
                await asyncio.wait_for(execution, definition.timeout)
            else:
                await execution
            run.state = WorkflowState.VERIFYING
            self._write_checkpoint(run)
            run.output = self._resolve(definition.outputs, run.variables) if definition.outputs else self._public_variables(run.variables)
            run.state = WorkflowState.SUCCEEDED
        except asyncio.CancelledError:
            run.state, run.error = WorkflowState.CANCELLED, "Workflow cancelled"
        except Exception as exc:
            run.state, run.error = WorkflowState.FAILED, str(exc)
        finally:
            await self._cancel_active(run.id)
            run.finished_at = _now()
            self._write_checkpoint(run)
            await self._emit("workflow_end", run, ctx, success=run.successful, error=run.error)
            self._cancel.pop(run.id, None)
            self._active.pop(run.id, None)
            self._semaphores.pop(run.id, None)
            self._continue.pop(run.id, None)
            self._tasks.pop(run.id, None)
        return run

    def start(self, definition: WorkflowDefinition, *, input=None, ctx=None, depth=0) -> str:
        """Start a workflow in the background and return a stable run id immediately."""
        run_id = make_id()
        task = asyncio.create_task(
            self.run(definition, input=input, ctx=ctx, depth=depth, run_id=run_id),
            name=f"workflow-{definition.name}-{run_id}",
        )
        self._tasks[run_id] = task
        return run_id

    def get_run(self, run_id: str) -> Optional[WorkflowRun]:
        return self._runs.get(run_id)

    async def _execute_steps(self, steps, definition, run, scope, ctx, depth, prefix):
        last = None
        for position, step in enumerate(steps):
            await self._control_point(run)
            key = f"{prefix}.{position}:{step.id}"
            last = await self._execute_step(step, definition, run, scope, ctx, depth, key)
            scope[step.id] = last
            run.variables[step.id] = last
            self._write_checkpoint(run)
        return last

    async def _execute_step(self, step, definition, run, scope, ctx, depth, key):
        """Run one static instruction inside one dynamic ExecutionFrame."""
        frame = run.frames.get(key) or ExecutionFrame(
            key=key, parent_key=self._parent_key(key), step_id=step.id,
            step_type=step.type, state=ExecutionState.READY,
            item_index=self._index_from_key(key), scope=self._safe_scope(scope),
        )
        run.frames[key] = frame
        frame.state, frame.started_at = ExecutionState.RUNNING, frame.started_at or _now()
        await self._emit(
            "workflow_frame_start", run, ctx, action_name=step.id,
            frame_key=key,
        )
        try:
            value = await self._execute_instruction(step, definition, run, scope, ctx, depth, key)
            invocation = run.invocations.get(key)
            frame.state = (
                ExecutionState.CACHED if invocation and invocation.state == InvocationState.CACHED
                else ExecutionState.SUCCEEDED
            )
            frame.output, frame.error = value, None
            return value
        except asyncio.CancelledError:
            frame.state, frame.error = ExecutionState.CANCELLED, "Cancelled"
            raise
        except Exception as exc:
            frame.state, frame.error = ExecutionState.FAILED, str(exc)
            raise
        finally:
            frame.finished_at = _now()
            await self._emit(
                "workflow_frame_end", run, ctx, action_name=step.id,
                success=frame.state in {ExecutionState.SUCCEEDED, ExecutionState.CACHED},
                error=frame.error, frame_key=key,
            )

    async def _execute_instruction(self, step, definition, run, scope, ctx, depth, key):
        if step.type in {StepType.AGENT, StepType.TOOL, StepType.SKILL, StepType.CONNECTOR, StepType.WORKFLOW}:
            return await self._call(step, definition, run, scope, ctx, depth, key)
        if step.type == StepType.PARALLEL:
            return await self._parallel(step.children, definition, run, scope, ctx, depth, key, step.concurrency)
        if step.type == StepType.MAP:
            items = self._as_list(self._resolve(step.items, scope))
            async def execute(item, index):
                local = {**scope, step.item_name: item, "index": index}
                item_key = f"{key}.item[{index}]"
                item_frame = self._ensure_virtual_frame(
                    run, item_key, key, step, local,
                    item_index=index,
                )
                try:
                    if step.target:
                        synthetic = step.model_copy(update={"type": StepType.AGENT, "args": {**step.args, step.item_name: "${" + step.item_name + "}"}})
                        value = await self._call(synthetic, definition, run, local, ctx, depth, f"{key}[{index}]")
                    else:
                        value = await self._execute_steps(step.children, definition, run, local, ctx, depth, f"{key}[{index}]")
                    item_frame.state, item_frame.output = ExecutionState.SUCCEEDED, value
                    return value
                except asyncio.CancelledError:
                    item_frame.state, item_frame.error = ExecutionState.CANCELLED, "Cancelled"
                    raise
                except Exception as exc:
                    item_frame.state, item_frame.error = ExecutionState.FAILED, str(exc)
                    raise
                finally:
                    item_frame.finished_at = _now()
            return await self._fanout(items, execute, step.concurrency or definition.max_concurrency, run.id)
        if step.type == StepType.BRANCH:
            selected = step.children if self._truth(step.condition, scope) else step.else_children
            skipped = step.else_children if selected is step.children else step.children
            self._mark_skipped(skipped, run, key)
            return await self._execute_steps(selected, definition, run, scope, ctx, depth, key)
        if step.type == StepType.LOOP:
            outputs, unchanged = [], 0
            previous = object()
            for round_number in range(step.max_rounds or 0):
                if step.condition_mode == "while" and not self._truth(step.condition, scope):
                    break
                local = {**scope, "loop": {"round": round_number + 1, "results": outputs}}
                round_frame = self._ensure_virtual_frame(
                    run, f"{key}.round[{round_number}]", key, step, local,
                    iteration=round_number,
                )
                try:
                    value = await self._execute_steps(step.children, definition, run, local, ctx, depth, f"{key}[{round_number}]")
                    round_frame.state, round_frame.output = ExecutionState.SUCCEEDED, value
                except asyncio.CancelledError:
                    round_frame.state, round_frame.error = ExecutionState.CANCELLED, "Cancelled"
                    raise
                except Exception as exc:
                    round_frame.state, round_frame.error = ExecutionState.FAILED, str(exc)
                    raise
                finally:
                    round_frame.finished_at = _now()
                outputs.append(value)
                scope.update({k: v for k, v in local.items() if k not in {"loop"}})
                if step.condition_mode == "until" and step.condition is not None and self._truth(step.condition, scope):
                    break
                unchanged = unchanged + 1 if value == previous else 0
                if step.no_progress_limit and unchanged >= step.no_progress_limit:
                    break
                previous = value
            return outputs
        if step.type in {StepType.REDUCE, StepType.VERIFY}:
            items = self._as_list(self._resolve(step.items, scope))
            if step.type == StepType.REDUCE:
                synthetic = step.model_copy(update={"type": StepType.AGENT, "args": {**step.args, "items": items}})
                return await self._call(synthetic, definition, run, scope, ctx, depth, key)
            async def verify(item, index):
                local = {**scope, step.item_name: item, "index": index}
                synthetic = step.model_copy(update={"type": StepType.AGENT, "args": {**step.args, step.item_name: item}})
                verdicts = await asyncio.gather(*[
                    self._call(synthetic, definition, run, local, ctx, depth, f"{key}[{index}].vote[{vote}]")
                    for vote in range(step.min_votes)
                ])
                return verdicts[0] if step.min_votes == 1 else {"item": item, "verdicts": verdicts}
            return await self._fanout(items, verify, step.concurrency or definition.max_concurrency, run.id)
        if step.type == StepType.CHECKPOINT:
            self._write_checkpoint(run)
            return {"checkpoint": run.checkpoint_path}
        raise ValueError(f"Unsupported workflow step: {step.type}")

    async def _parallel(self, steps, definition, run, scope, ctx, depth, key, concurrency):
        async def execute(step, index):
            local = dict(scope)
            value = await self._execute_step(step, definition, run, local, ctx, depth, f"{key}[{index}]")
            return step.id, value
        pairs = await self._fanout(steps, execute, concurrency or definition.max_concurrency, run.id)
        result = dict(pairs)
        scope.update(result)
        return result

    async def _fanout(self, items, callback, concurrency, run_id):
        semaphore = asyncio.Semaphore(concurrency)
        async def guarded(item, index):
            async with semaphore:
                await self._control_point(self._runs[run_id])
                return await callback(item, index)
        tasks = [asyncio.create_task(guarded(item, index)) for index, item in enumerate(items)]
        self._active[run_id].update(tasks)
        try:
            return await asyncio.gather(*tasks)
        finally:
            self._active[run_id].difference_update(tasks)

    async def _call(self, step, definition, run, scope, ctx, depth, key):
        cached = run.invocations.get(key)
        if cached and cached.state in {InvocationState.COMPLETED, InvocationState.CACHED}:
            cached.state, cached.cached = InvocationState.CACHED, True
            return cached.output
        if run.agent_count >= definition.max_agents:
            raise RuntimeError(f"Workflow exceeded max-agents={definition.max_agents}")
        if step.type in {StepType.AGENT}:
            run.agent_count += 1
        args = self._resolve(step.args, scope)
        task_text = self._resolve(step.task, scope)
        invocation_input = {**args, **({"task": task_text} if task_text else {})}
        record = cached or InvocationRun(
            key=key, frame_key=(key if key in run.frames else self._parent_key(key) or key), capability_type=step.type,
            capability_name=step.target or "", input=invocation_input,
        )
        run.invocations[key] = record
        record.state, record.started_at = InvocationState.QUEUED, record.started_at or _now()
        await self._emit(
            "workflow_node_start", run, ctx, action_name=step.target, input=step.args,
            frame_key=record.frame_key, invocation_key=key,
        )
        for attempt in range(step.retries + 1):
            attempt_run = InvocationAttempt(number=attempt + 1, state=InvocationState.ACQUIRING_SLOT)
            record.attempts.append(attempt_run)
            try:
                await self._control_point(run)
                record.state = InvocationState.ACQUIRING_SLOT
                async with self._semaphores[run.id]:
                    await self._control_point(run)
                    record.state = attempt_run.state = InvocationState.STARTING
                    attempt_run.started_at = _now()
                    record.state = attempt_run.state = InvocationState.RUNNING
                    value = await self._invoke(step.type, step.target, task_text, args, ctx, depth)
                record.output = self._normalize(value)
                record.state, record.error = InvocationState.COMPLETED, None
                attempt_run.state, attempt_run.finished_at = InvocationState.COMPLETED, _now()
                break
            except asyncio.CancelledError:
                record.state, record.error = InvocationState.CANCELLED, "Cancelled"
                attempt_run.state, attempt_run.error, attempt_run.finished_at = InvocationState.CANCELLED, "Cancelled", _now()
                record.finished_at = _now()
                await self._emit(
                    "workflow_node_end", run, ctx, action_name=step.target, success=False,
                    error="Cancelled", frame_key=record.frame_key, invocation_key=key,
                )
                raise
            except Exception as exc:
                record.error = str(exc)
                attempt_run.error, attempt_run.finished_at = str(exc), _now()
                if attempt >= step.retries:
                    record.state = attempt_run.state = InvocationState.FAILED
                    record.finished_at = _now()
                    await self._emit(
                        "workflow_node_end", run, ctx, action_name=step.target,
                        success=False, error=str(exc), frame_key=record.frame_key,
                        invocation_key=key,
                    )
                    raise
                record.state = attempt_run.state = InvocationState.RETRYING
                frame = run.frames.get(key)
                if frame:
                    frame.state = ExecutionState.RETRY_WAIT
                await asyncio.sleep(0)
        record.finished_at = _now()
        await self._emit(
            "workflow_node_end", run, ctx, action_name=step.target, success=True,
            frame_key=record.frame_key, invocation_key=key,
        )
        return record.output

    async def _invoke(self, kind, target, task, args, ctx, depth):
        payload = dict(args or {})
        if task:
            payload.setdefault("task", task)
        if kind == StepType.AGENT:
            from agentevolver.agent import agent_manager
            return await agent_manager(name=target, input=payload, ctx=ctx)
        if kind == StepType.TOOL:
            from agentevolver.tool import tool_manager
            return await tool_manager(name=target, input=payload, ctx=ctx)
        if kind == StepType.SKILL:
            from agentevolver.skill import skill_manager
            return await skill_manager(name=target, input=payload, ctx=ctx)
        if kind == StepType.CONNECTOR:
            from agentevolver.connector import connector_manager
            action = payload.pop("action", None)
            return await connector_manager(name=target, input={"action": action, "args": payload}, ctx=ctx)
        if kind == StepType.WORKFLOW:
            from .server import workflow_manager
            return await workflow_manager.run(target, input=payload, ctx=ctx, depth=depth + 1)
        raise ValueError(f"{kind.value} is not directly callable")

    def _resolve(self, value, scope):
        if isinstance(value, dict):
            return {key: self._resolve(item, scope) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve(item, scope) for item in value]
        if not isinstance(value, str):
            return value
        exact = _EXPR.fullmatch(value)
        if exact:
            return self._path(exact.group(1).strip(), scope)
        return _EXPR.sub(lambda match: str(self._path(match.group(1).strip(), scope)), value)

    def _truth(self, expression, scope):
        if expression is None:
            return False
        if isinstance(expression, str):
            stripped = expression.strip()
            if stripped.startswith("not "):
                return not bool(self._resolve("${" + stripped[4:].strip("${}") + "}", scope))
        return bool(self._resolve(expression, scope))

    @staticmethod
    def _path(path, scope):
        current: Any = scope
        for part in path.split("."):
            if isinstance(current, dict):
                if part not in current:
                    raise ValueError(f"Unknown workflow reference: ${{{path}}}")
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                current = current[int(part)]
            else:
                current = getattr(current, part)
        return current

    @staticmethod
    def _normalize(value):
        if isinstance(value, Response):
            if not value.success:
                raise RuntimeError(value.message)
            return {"message": value.message, "data": value.data, "files": value.file_path}
        if isinstance(value, WorkflowRun):
            if not value.successful:
                raise RuntimeError(value.error or "Nested workflow failed")
            return value.output
        return value

    @staticmethod
    def _as_list(value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("Dynamic fan-out items must resolve to a list")
        return value

    @staticmethod
    def _validate_inputs(definition, values):
        result = dict(values)
        expected = {
            "string": str, "array": list, "object": dict,
            "boolean": bool, "integer": int, "number": (int, float),
        }
        for name, spec in definition.inputs.items():
            if name not in result and spec.default is not None:
                result[name] = spec.default
            if spec.required and name not in result:
                raise ValueError(f"Missing required workflow input: {name}")
            if name in result and spec.type in expected and not isinstance(result[name], expected[spec.type]):
                raise ValueError(f"Workflow input {name!r} must be {spec.type}")
        return result

    @staticmethod
    def _public_variables(values):
        return {key: value for key, value in values.items() if key != "inputs"}

    def cancel(self, run_id):
        event = self._cancel.get(run_id)
        if event is None:
            return False
        run = self._runs.get(run_id)
        if run:
            run.state = WorkflowState.CANCELLING
        event.set()
        # A paused coroutine is waiting on the continue event, so wake it to let the
        # next control-point cancellation check terminate the run.
        continue_event = self._continue.get(run_id)
        if continue_event is not None:
            continue_event.set()
        for task in self._active.get(run_id, set()):
            task.cancel()
        return True

    def pause(self, run_id: str) -> bool:
        """Request a cooperative pause at the next safe instruction boundary."""
        event = self._continue.get(run_id)
        run = self._runs.get(run_id)
        if event is None or run is None or run.state != WorkflowState.RUNNING:
            return False
        run.state = WorkflowState.PAUSING
        event.clear()
        return True

    def continue_run(self, run_id: str) -> bool:
        """Continue an in-memory paused run without rebuilding completed work."""
        event = self._continue.get(run_id)
        run = self._runs.get(run_id)
        if event is None or run is None or run.state not in {WorkflowState.PAUSED, WorkflowState.PAUSING}:
            return False
        run.state = WorkflowState.RESUMING
        event.set()
        return True

    async def _control_point(self, run: WorkflowRun) -> None:
        """Cooperative pause/cancel boundary; active manager calls finish atomically."""
        if self._cancel[run.id].is_set():
            raise asyncio.CancelledError
        event = self._continue[run.id]
        if not event.is_set():
            run.state, run.paused_at = WorkflowState.PAUSED, _now()
            self._write_checkpoint(run)
            await event.wait()
            if self._cancel[run.id].is_set():
                raise asyncio.CancelledError
            run.state, run.paused_at = WorkflowState.RUNNING, None

    async def _cancel_active(self, run_id):
        tasks = list(self._active.get(run_id, set()))
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _parent_key(key: str) -> Optional[str]:
        return key.rsplit(".", 1)[0] if "." in key else None

    @staticmethod
    def _index_from_key(key: str) -> Optional[int]:
        match = re.search(r"\[(\d+)\](?!.*\[)", key)
        return int(match.group(1)) if match else None

    @staticmethod
    def _safe_scope(scope: Dict[str, Any]) -> Dict[str, Any]:
        """Keep useful local bindings without duplicating every global result per frame."""
        return {
            key: value for key, value in scope.items()
            if key in {"item", "index", "loop"} or not isinstance(value, (dict, list))
        }

    @staticmethod
    def _ensure_virtual_frame(
        run, key, parent_key, step, scope, *, item_index=None, iteration=None,
    ) -> ExecutionFrame:
        frame = run.frames.get(key) or ExecutionFrame(
            key=key, parent_key=parent_key, step_id=step.id, step_type=step.type,
            state=ExecutionState.RUNNING, item_index=item_index, iteration=iteration,
            scope=WorkflowRuntime._safe_scope(scope), started_at=_now(),
        )
        run.frames[key] = frame
        return frame

    @staticmethod
    def _mark_skipped(steps, run, parent_key):
        """Materialize non-selected branch instructions for honest trace/status views."""
        for index, step in enumerate(steps):
            key = f"{parent_key}.skipped[{index}]:{step.id}"
            run.frames[key] = ExecutionFrame(
                key=key, parent_key=parent_key, step_id=step.id, step_type=step.type,
                state=ExecutionState.SKIPPED, finished_at=_now(),
            )

    @staticmethod
    def _checkpoint_path(run, ctx):
        workspace = Path(getattr(ctx, "workspace_root", None) or os.getcwd()).resolve()
        root = workspace / ".agentevolver" / "workflows"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{run.id}.json"

    @staticmethod
    def _write_checkpoint(run):
        if not run.checkpoint_path:
            return
        path = Path(run.checkpoint_path)
        fd, temporary = tempfile.mkstemp(prefix=f".{run.id}-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(run.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    async def _emit(
        event_type, run, ctx, *, action_name=None, input=None, success=None,
        error=None, frame_key=None, invocation_key=None,
    ):
        from agentevolver.trace import trace_manager
        from agentevolver.trace.types import TraceEvent, TraceEventType
        await trace_manager.emit(TraceEvent(
            event_type=TraceEventType(event_type), session_id=getattr(ctx, "session_id", None),
            task_id=run.id, action_type="workflow", action_name=action_name or run.workflow_name,
            label=action_name or run.workflow_name, input=input, success=success, error=error,
            metadata={
                "workflow": run.workflow_name, "runtime_version": run.runtime_version,
                "frame_key": frame_key, "invocation_key": invocation_key,
            },
        ))


workflow_runtime = WorkflowRuntime()

__all__ = ["WorkflowRuntime", "workflow_runtime"]
