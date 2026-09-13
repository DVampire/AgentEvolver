"""Lightweight component calls owned by the existing process kernel.

No mailbox, model turn or second Agent scheduler lives here. Managers supply bound
operations; this service coordinates their resources and joins their cancellation.
"""
from __future__ import annotations

import asyncio
import inspect
import time
import uuid
import weakref
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable


CURRENT_RUNTIME = ContextVar("component_runtime", default=None)
CURRENT_OWNER_CONTEXT = ContextVar("component_owner_context", default=None)
_CURRENT = ContextVar("component_invocation", default=None)


def runtime():
    current = CURRENT_RUNTIME.get()
    if current is not None:
        return current
    from agentevolver.runtime.kernel import kernel
    return kernel.calls


def owner_id(ctx) -> str:
    extra = getattr(ctx, "extra", {}) or {}
    return str(extra.get("process_pid") or getattr(ctx, "id", "") or "")


def current_owner():
    call = _CURRENT.get()
    return call.owner if call else owner_id(CURRENT_OWNER_CONTEXT.get())


def current_context():
    call = _CURRENT.get()
    return getattr(call, "ctx", None) if call else CURRENT_OWNER_CONTEXT.get()


async def run_blocking(function, *args, **kwargs):
    """Offload a blocking backend operation, joining it even if its caller is cancelled."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    return await _join(task)


async def _join(task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve an error too, so cancellation never leaves an unobserved task.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def call_function(function, *args, **kwargs):
    """Run an entity method without making synchronous implementations block the loop.

    Threads cannot be forcibly stopped: cancellation joins them before releasing claims.
    CPU-bound Python requiring speedup or hard cancellation needs a process backend.
    """
    if inspect.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    result = await run_blocking(function, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def allows_parallel(module, info, operation=None):
    """Project the Manager's execution declaration into Agent batch admission.

    Effects remain a separate permission contract. Legacy action overrides are honoured.
    """
    if module in {"skill", "agent", "capability_search"}:
        return True
    instance = getattr(info, "instance", None)
    def declaration(key, default=None):
        if instance is not None:
            return getattr(instance, key, default)
        configured = getattr(info, "config", {}) or {}
        if key in configured:
            return configured[key]
        field = getattr(getattr(info, "cls", None), "model_fields", {}).get(key)
        return field.default if field is not None else default
    metadata = dict(getattr(info, "metadata", {}) or {})
    if module == "environment":
        action = (getattr(info, "actions", {}) or {}).get(operation)
        metadata = dict(getattr(action, "metadata", {}) or {})
    if isinstance(metadata.get("parallel_safe"), bool):
        return metadata["parallel_safe"]
    if module == "connector":
        return metadata.get("concurrent") is True
    return declaration("concurrent", False) is True or (
        module == "environment" and declaration("state_scope") == "call")


@dataclass(frozen=True)
class ResourceClaim:
    key: str
    shared: bool = False

    @classmethod
    def path(cls, path, *, shared=False):
        return cls("path:" + str(Path(path).expanduser().resolve()), shared)

    def conflicts(self, other):
        if self.shared and other.shared:
            return False
        if self.key == other.key:
            return True
        if self.key.startswith("path:") and other.key.startswith("path:"):
            a, b = Path(self.key[5:]), Path(other.key[5:])
            return a in b.parents or b in a.parents
        return False


@dataclass
class Invocation:
    id: str
    module: str
    name: str
    version: str
    owner: str
    parent: str
    claims: tuple
    state: str = "queued"
    queued_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    task: Any = None
    internal: bool = False
    bindings: list = field(default_factory=list)

    def public(self):
        return {key: getattr(self, key) for key in (
            "id", "module", "name", "version", "owner", "parent", "state",
            "queued_at", "started_at", "finished_at", "internal")}


class _LoopState:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.active = {}
        self.waiting = []
        self.running = {}
        self.history = deque(maxlen=256)
        self.bindings = {}
        self.cleanups = {}
        self.closed = set()
        self.closing = False
        self.shutdown_task = None


class InvocationRuntime:
    def __init__(self):
        self._loops = weakref.WeakKeyDictionary()

    def _state(self):
        loop = asyncio.get_running_loop()
        if loop not in self._loops:
            self._loops[loop] = _LoopState()
        return self._loops[loop]

    def open_owner(self, owner):
        state = self._state()
        if owner in state.closed and any(key[3] == owner for key in state.bindings):
            raise RuntimeError(f"Execution owner {owner!r} still has resources awaiting cleanup")
        state.closed.discard(owner)

    def own(self, module, name, owner, value, close):
        """Attach already-created jobs/PTYs without inventing a second job registry."""
        if owner:
            self._state().bindings[(module, name, "", owner)] = (value, close)

    async def invoke(self, module: str, name: str, body: Callable[[], Awaitable[Any]],
                     *, ctx=None, version="", claims=(), timeout=None, call_id=None,
                     max_concurrency=None, limit_key=None, internal=False, owner_scoped=False):
        state = self._state()
        if state.closing and not internal:
            raise RuntimeError("Component runtime is shutting down")
        # Explicit background jobs belong to the Agent, not the short submit action.
        # Their producer must register the task before returning its job handle.
        parent = None if owner_scoped else _CURRENT.get()
        ctx = ctx if ctx is not None else current_context()
        owner = owner_id(ctx) or current_owner()
        if owner_scoped and not owner:
            raise ValueError("Owner-scoped work requires an explicit execution owner")
        if owner and owner in state.closed and not internal:
            raise RuntimeError(f"Execution owner {owner!r} is closing")
        claims = tuple(claims)
        # A nested call cannot wait for an exclusive resource held by its ancestor.
        ancestor = parent
        while ancestor:
            if any(a.conflicts(b) for a in claims for b in ancestor.claims):
                raise RuntimeError("Nested component call conflicts with an ancestor resource")
            ancestor = state.active.get(ancestor.parent)
        record = Invocation(call_id or uuid.uuid4().hex, module, name, str(version),
                            owner, parent.id if parent else owner, claims, internal=internal)
        record.ctx = ctx
        if record.id in state.active:
            raise RuntimeError(f"Duplicate active invocation {record.id}")
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        bucket = limit_key or (module, name)
        if max_concurrency is not None and any(
                getattr(entry, "bucket", None) == bucket and entry.limit is not None
                and entry.limit != max_concurrency
                for entry in state.active.values()):
            raise ValueError("Active calls in a concurrency group must use the same capacity")
        record.bucket = bucket
        record.limit = max_concurrency
        state.active[record.id] = record

        def blockers(entry):
            earlier = state.waiting[:state.waiting.index(entry)]
            blocked = [other for other in (*state.running.values(), *earlier)
                       if any(a.conflicts(b) for a in entry.claims for b in other.claims)]
            peers = [other for other in state.running.values()
                     if other.bucket == entry.bucket and other.limit is not None]
            if entry.limit is not None and len(peers) >= entry.limit:
                blocked.extend(peers)
            return blocked

        def would_deadlock(blocked):
            # A running orchestration may be awaiting its nested calls. Include those
            # edges to catch capacity exhaustion and cross-branch A -> B -> A waits.
            pending, seen = list(blocked), set()
            while pending:
                entry = pending.pop()
                if entry.id == record.id:
                    return True
                if entry.id in seen:
                    continue
                seen.add(entry.id)
                if entry in state.waiting:
                    pending.extend(blockers(entry))
                else:
                    pending.extend(child for child in state.active.values() if child.parent == entry.id)
            return False

        async def run():
            token = _CURRENT.set(record)
            rt_token = CURRENT_RUNTIME.set(self)
            try:
                async with state.condition:
                    state.waiting.append(record)
                    def ready():
                        if owner and owner in state.closed and not internal:
                            return True
                        blocked = blockers(record)
                        if would_deadlock(blocked):
                            raise RuntimeError("Nested component resource/capacity dependency would deadlock")
                        return not blocked
                    await state.condition.wait_for(ready)
                    if owner and owner in state.closed and not internal:
                        raise RuntimeError(f"Execution owner {owner!r} is closing")
                    state.waiting.remove(record)
                    record.bucket = bucket
                    state.running[record.id] = record
                    record.state, record.started_at = "running", time.monotonic()
                result = await body()
                record.state = "completed" if getattr(result, "success", True) else "failed"
                return result
            except asyncio.CancelledError:
                record.state = "cancelled"
                raise
            except BaseException:
                record.state = "failed"
                raise
            finally:
                # Detached nested calls must not outlive their owning invocation.
                children = [entry.task for entry in tuple(state.active.values())
                            if entry.parent == record.id and entry.task is not None]
                for child in children:
                    if not child.cancelling():
                        child.cancel()
                if children:
                    await asyncio.gather(*children, return_exceptions=True)
                try:
                    errors = []
                    cancelled = False
                    for key in reversed(record.bindings):
                        try:
                            await self._close_binding(key, join=True)
                        except asyncio.CancelledError:
                            cancelled = True
                        except Exception as error:
                            errors.append(error)
                    if errors:
                        raise ExceptionGroup("Call resource cleanup failed", errors)
                    if cancelled:
                        raise asyncio.CancelledError
                except asyncio.CancelledError:
                    record.state = "cancelled"
                    raise
                except BaseException:
                    record.state = "failed"
                    raise
                finally:
                    async with state.condition:
                        if record in state.waiting:
                            state.waiting.remove(record)
                        state.running.pop(record.id, None)
                        state.active.pop(record.id, None)
                        record.finished_at = time.monotonic()
                        state.history.append(record.public())
                        state.condition.notify_all()
                    CURRENT_RUNTIME.reset(rt_token)
                    _CURRENT.reset(token)

        record.task = asyncio.create_task(run(), name=f"{module}:{name}:{record.id}")
        try:
            return await asyncio.wait_for(record.task, timeout)
        finally:
            # Cancellation before the task first runs still leaves no queued record.
            if record.task.done() and record.id in state.active:
                state.active.pop(record.id, None)

    async def bind(self, module, name, version, owner, factory, close, *, scope="owner"):
        """Publish one ready binding; existing backends may return an opaque session."""
        if not owner:
            raise ValueError("Stateful resources require an explicit owner context")
        invocation = _CURRENT.get()
        if scope == "call":
            if invocation is None:
                raise ValueError("Call-scoped resources must be accessed through a Manager invocation")
            name = f"{name}:call:{invocation.id}"
        elif scope != "owner":
            raise ValueError(f"Unknown binding scope: {scope!r}")
        key = (module, name, str(version), owner)
        state = self._state()
        async def create():
            if key not in state.bindings:
                value = await factory()
                state.bindings[key] = (value, close)
                if scope == "call":
                    invocation.bindings.append(key)
            return state.bindings[key][0]
        from types import SimpleNamespace
        return await self.invoke(module, name + ":bind", create,
                                 ctx=SimpleNamespace(id=owner, extra={}),
                                 version=version,
                                 claims=(ResourceClaim("binding:" + repr(key)),))

    async def _close_binding(self, key, *, join=False):
        """Close once; retain failed or unfinished resources for a later owner cleanup."""
        state = self._state()
        if key not in state.bindings:
            return
        value, close = state.bindings[key]
        if key not in state.cleanups:
            async def cleanup():
                result = close(value)
                if inspect.isawaitable(result):
                    await result
            state.cleanups[key] = asyncio.create_task(cleanup())
        task = state.cleanups[key]
        try:
            if join:
                await _join(task)
            else:
                await asyncio.wait_for(asyncio.shield(task), 10)
        except BaseException:
            if task.done():
                state.cleanups.pop(key, None)
                if not task.cancelled() and task.exception() is None:
                    state.bindings.pop(key, None)
            raise
        state.cleanups.pop(key, None)
        state.bindings.pop(key, None)

    async def release(self, *, owner=None, module=None):
        state = self._state()
        if owner:
            state.closed.add(owner)
        current = _CURRENT.get()
        tasks = [entry.task for entry in tuple(state.active.values())
                 if (owner is None or entry.owner == owner)
                 and (module is None or entry.module == module)
                 and entry is not current and entry.task is not None]
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        errors = []
        for key, (value, close) in tuple(state.bindings.items()):
            if (owner is None or key[3] == owner) and (module is None or key[0] == module):
                try:
                    await self._close_binding(key)
                except Exception as error:
                    errors.append(error)
        if errors:
            raise ExceptionGroup("Component resource cleanup failed", errors)

    async def shutdown(self, timeout=10):
        state = self._state()
        state.closing = True
        task = state.shutdown_task
        if task is None:
            task = state.shutdown_task = asyncio.create_task(self.release())
        done, _ = await asyncio.wait([task], timeout=max(0, timeout))
        if task in done:
            state.shutdown_task = None
            error = task.exception()
            if error is None:
                state.closing = False
                return []
        # IDs refer to the same records snapshot() exposes. Failed bindings remain
        # named until release succeeds, even if their invocation already ended.
        return list(state.active) + [f"resource:{key!r}" for key in state.bindings]

    def snapshot(self):
        state = self._state()
        return [*state.history, *(entry.public() for entry in state.active.values())]


def invocation_claims(instance, ctx, arguments, *, module, name):
    """An implementation's declaration; unknown shared instances stay exclusive."""
    resolver = getattr(instance, "resource_claims", None)
    if callable(resolver):
        return tuple(resolver(ctx, arguments))
    if getattr(instance, "concurrent", False):
        return ()
    return (ResourceClaim(f"{module}:{name}"),)
