# Runtime concurrency across all eight component families

Status: implemented and locally regression-tested, 2026-09-14.
This document replaces `environment-concurrency.md`. Experiments remain paused.

## Architecture

The existing `agentevolver/runtime` coordinates execution. Context Managers resolve a
registered version and adapt its bound implementation to runtime. Components own domain
state and cleanup. There is no separate environment scheduler or capability scheduler.

```text
Agent / Workflow / Python caller
  -> component Manager: version, arguments, permissions, bound operation
  -> runtime: owner, admission, resource claims, cancellation and joining
  -> implementation: actual operation and state
  -> existing result / execution receipt
```

Agent execution retains `Kernel`, `Process`, mailbox, turns and `RunBudget`. Lightweight
component calls use `Kernel.calls` (`runtime/invocation.py`), without creating Agent
processes or a second mailbox. Invocation records contain identity, version, parent,
owner, state and queue/start/end times. They do not duplicate Trace or charge model usage.

| Entity | Implemented adapter | State and concurrency contract |
| --- | --- | --- |
| Tool | `ToolContextManager.invoke_config` and existing `ToolExecutionPipeline` | Default exclusive instance access; verified reentrant tools opt in. Declared filesystem writes claim the workspace. Preparation, body and result finalization share admission. |
| Skill | `SkillContextManager.__call__` | Concurrent instruction snapshots. Scripts later run through their actual Tool/Environment. |
| Connector | `ConnectorContextManager` and the existing execution pipeline | Conservative shared-service exclusion; `metadata.concurrent=true` permits reentrant requests. Current MCP transport sessions remain per call. |
| Plugin | `PluginContextManager` | Single-flight client initialization; shared clients by default, or `state_scope="owner"` for separate instances. Tools can access trusted invocation context without storing it on a shared object. |
| Agent | `AgentContextManager` -> existing kernel spawn/dispatch/wait | Fresh child instances and IDs; resident turns remain sequential. Parent dispatch retains shared budget and grants. Foreground cancellation stops and joins its process. |
| Workflow | Existing `WorkflowRuntime` driven through `Kernel.calls` | Keeps graph dependencies, branch limits and checkpoints. Agent nodes share the root workspace but have independent contexts and input directories. Standalone workflow scopes release their bindings on exit. |
| Environment | `EnvironmentContextManager`, including actions, state and live views | Fresh instance per owner by default; owner operations serialize. Independent evaluators can select a fresh instance per call, cleaned before admission is released. Multiplexed builtins retain their existing state maps and close one owner's session at exit. |
| Memory | `MemoryContextManager.invoke`, including legacy entry points and Trace projection | Unknown backends serialize. `TieredMemory` retains its session transactions and revision-checked compaction; ingestion can proceed while a summary is generated. Trace projection is internal and does not emit another ordinary call lifecycle. |

## Implemented API

Use public Managers for execution. `get()` without an execution context exposes a
registered definition/backend for inspection; calling its methods directly bypasses
admission. Environment `get(name, ctx=ctx)` returns an owner-bound instance, while its
operations should still enter through the Manager.

`runtime()` resolves the current kernel's call service. `owner_id(ctx)` uses trusted
`ctx.extra.process_pid`, falling back to `ctx.id` for explicit non-Agent scopes. Callers
must reuse the same context to retain interactive state. Model arguments cannot select
another owner. Context conversion copies argument dictionaries without copying live handles.

```python
from agentevolver.runtime.invocation import ResourceClaim, runtime

result = await runtime().invoke(
    "environment", "evaluate", bound_operation,
    ctx=ctx, version=version,
    claims=(ResourceClaim.path(features, shared=True),
            ResourceClaim.path(trial_output)),
    max_concurrency=4,
    limit_key=("environment", "evaluator"),
)
```

Managers supply this adapter; generated entities normally only implement their base class.
Environment methods use shared `call_function`: await async methods, offload sync methods to
a thread, and join cancellation before releasing claims. This is loop responsiveness, not
a GIL-bound CPU speedup or forced termination guarantee.

- `ResourceClaim(key)` is exclusive; `shared=True` permits shared readers. Path claims
  resolve symlinks and detect directory/descendant conflicts across entity families.
  Aliases in a remote/container namespace require the implementation to supply a common key.
- `concurrent=True` declares instance reentrancy, not permission or automatic dependency
  discovery. Environment/Plugin owner instances otherwise serialize per owner; Tools and
  unknown shared clients conservatively serialize per component.
- Environment `state_scope="call"` binds fresh state to one invocation. Declarative action
  `read_paths` / `write_paths` name artifact arguments; Manager resolves them before permissions
  and adds shared/exclusive path claims. Advanced adapters may override
  `resource_claims(ctx, arguments, operation)`; ordinary implementations do not need that hook.
  Environments may set `max_concurrency`. A shared `concurrency_group` bounds combined
  active work across environments; all limited members use the same capacity. Tool/Plugin
  use the two-argument claims form.
- Agent batches derive admission from the same `concurrent` / `state_scope="call"` declaration
  as Manager execution, including lazy client class defaults. Legacy `parallel_safe` overrides
  remain supported. `read_only` still describes effects; runtime claims decide actual overlap.
- Short status/cancel actions may declare `capacity_exempt=True`; these calls neither wait
  for nor consume worker slots. Claims and permissions still apply, so control state must
  not require an exclusive resource held throughout the computation it needs to stop.
- `bind(module, name, version, owner, factory, close)` publishes one fully initialized
  binding. Owner and version are part of its identity; running calls keep their selected
  definition. `scope="call"` binds lifetime to the current invocation and retains failed cleanup
  for owner-exit retry. Failed initialization must clean partially created resources before raising.
- `own(...)` attaches an existing Job/Terminal handle to its owner, using the existing job
  and PTY registries rather than introducing another process registry.
- Registered background workers use `invoke(..., owner_scoped=True)` to outlive a short
  submit action while remaining joined on owner exit. Foreground nested calls retain their
  parent lifetime. Register the task before returning its handle and do not await workers
  while the submitter holds their resource or worker slot. Agent lifecycle hooks carry the
  owner context too, so jobs created during startup are cleaned up on exit.
- `release(owner=...)` cancels and joins calls, then closes that owner's bindings. Failed
  cleanup remains tracked for retry. `shutdown(timeout=...)` bounds the wait and returns
  outstanding invocation/resource IDs; a timeout does not claim that an external resource
  stopped. Kernel shutdown includes this drain.

Admission detects ancestor conflicts and cycles involving nested resources or capacity.
It rejects unsupported dependency cycles rather than waiting indefinitely. Orchestration
has no global worker permit to monopolize while waiting for children. Same-resource
waiters queue; unrelated resources can proceed. Cancellation joins nested cleanup without
repeatedly cancelling the child's `finally` block.

## Builtin environments

| Environment | Changes |
| --- | --- |
| SSH | Owner-specific shells and selected hosts; connection, tmux and view-port namespaces also separate environment instances. Single-flight startup; owner cleanup removes only its cached view/shell entries and retains failed cleanup. |
| Browser | Private contexts/pages and command scope; single-flight page creation; same-owner actions and observations serialize. VNC uses the common owner-instance binding for separate displays/containers. |
| Computer | Desktop container keyed by definition and owner; complete input/observation operations serialize. Failed desktop startup releases its acquisition. |
| Godot | Owner-specific game state and Bash container routes; project import/check/run/export operations coordinate through shared workspace claims. Cleanup removes only its own route. |
| Terminal | Separate PTYs overlap within an owner; sends to one PTY serialize and signal/close remain available. Foreign handles fail and owner exit closes owned PTYs. |
| Job | Wait does not block stop. Owner checks deny sibling access through a shared session; Kernel child process stop joins exit. Owner startup hooks and registered background tasks retain cleanup ownership. |
| Artifact renderer | Owner-specific pages; guarded acquisition; private contexts and definition/owner sandbox bindings. Failed acquisition releases partial resources. |

Gateway environment opening uses the same binding path. VNC relay URLs include an owner
key so two owners of the same environment do not replace each other's relay target.

## Verification and limits

Regression coverage includes independent overlap, shared-resource serialization, path
conflicts, capacity limits, nested dependency rejection, acquisition races, owner/version
isolation, cancellation during initialization, cleanup after Agent startup failure,
shutdown deadlines, Plugin context isolation, reentrant Connector requests, real-kernel
Workflow Agent siblings/cancellation and owner-specific VNC relay targets. Existing Agent,
Tool, Connector, Memory, Browser, Godot, SSH, Job, Terminal, Workflow and Gateway suites
are also used for integration checks. Browser checks include local Chromium.

The earlier unified-runtime integration passed 510 tests. This environment follow-up's
broad integration passed 381 tests (3 skipped, 5 deselected), with one outdated SSH prompt
assertion: the native action name is supplied by environment instructions, while the actor
prompt describes delivery. That test now checks both sources. After the final process-stop
and SSH changes, the affected regression set passed all 219 tests (3 skipped). Counts overlap.
Changed Python files pass syntax parsing and `git diff --check` is clean.

`tests/test_environment_concurrency.py` adds 16 cases exercising all seven builtin adapters,
the executable skill template, shared evaluator capacity, control calls under saturation,
registered background worker lifetime and blocking process cleanup. Chromium and numerical
subprocesses run locally; SSH, Docker and desktop transport dependencies are mocked.

Workflow's unrelated hard-coded `.versions` archive-path assertion was previously reproduced
on the pre-change commit `d4b552e4`; it is outside this environment follow-up.

The scope is one host process/event loop. Shared database/filesystem coordination across
processes requires backend transactions, real file locks or isolated storage. Root-session
rebinding remains constrained by the existing path leases; separate experiments still
need separate host processes. This change does not turn a blocking calculation into async
work or provide a distributed worker pool.

Separate shells/desktops do not imply separate filesystems. Shared workspaces remain an
explicit collaboration resource. A command or background job which continues after its
invocation must own distinct outputs or coordinate commits in its implementation; a short
invocation claim is not a lifetime lock on all future subprocess writes. Remote cancellation
and resource reclamation require backend-specific verification. No remote SSH/Docker
experiment is run by these tests. Numerical overlap is checked using real local subprocesses
with deterministic outputs; this verifies admission and parity, not financial-engine
correctness or a production speedup.

Generated entities inherit this contract from `self_evolving_skill/references/conventions.md`
and its environment template. Numerical evaluators should publish immutable shared inputs,
run bounded jobs with separate trial state/output, and commit result indexes atomically.
