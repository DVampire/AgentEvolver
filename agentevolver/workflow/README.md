# AgentEvolver Dynamic Workflow

Workflow module version: **1.4.0**  
HTML schema version: **1.0.0**  
Runtime/checkpoint version: **1.0.0**

This package turns reviewable HTML into a dynamic multi-agent program. A workflow is
system orchestration infrastructure, not an Agent subtype and not a fixed DAG.

## Modules

| Module | Responsibility |
|---|---|
| `types.py` | Versioned definitions, steps, runs, frames, invocations, attempts, and states |
| `compiler.py` | Parse the restricted HTML language and reject unsafe/unbounded programs |
| `context.py` | Discovery, registry state, compact prompt roster, native schemas, caches, and evaluation evidence |
| `runtime.py` | Interpret control flow, enforce budgets, invoke capabilities, checkpoint, pause, and resume |
| `server.py` | Thin registry facade plus Runtime run/start/pause/resume/cancel control |
| `default/` | Versioned built-in Workflow HTML documents |

## Capability model

Workflow follows the same registry model as Tool and Skill. Every active registration is
projected to MetaAgent as a native function named `workflow__<name>` using the HTML
input contract. The prompt receives only a compact roster. The read-only
`inspect_workflow` tool supplies full HTML, compiled nodes, source location, and registry
facts on demand.

As with Tool and Skill, `WorkflowContextManager` owns non-execution lifecycle state and
`WorkflowManagerServer` is the stable public facade. `WorkflowRuntime` separately owns
run state, so registry context never becomes an execution scheduler.

Both manager layers follow the framework convention: Pydantic `BaseModel` configuration,
declared `base_dir`, lazy `_ensure_context_manager()` creation in the Server, active and
historical version maps in Context, cache invalidation, restore, cleanup, logging, and
VersionManager registration during discovery.

Generation, optimization, evaluation, and rollback remain management/evolution
operations. They are intentionally not multiplexed through a generic Workflow tool.

The self-evolution layer mirrors Skill: `workflow_creator_skill` contains the shared
methodology; thin generate/optimize/evaluate Agents perform one phase each; a registration
Hook registers validated HTML live, matching Tool/Skill; persisted version-scoped evidence
guides keep/optimize/rollback decisions through the common `evolution_tool`.

## Execution model

```text
WorkflowDefinition (static HTML program)
  └─ ExecutionFrame (one dynamic map item / loop round / branch / step)
      └─ InvocationRun (one concrete capability call)
          └─ InvocationAttempt (one retry attempt)
```

The HTML program decides control flow. State machines provide reliable scheduling,
observability, cancellation, pause/resume, caching, and recovery.

## State machines

Workflow runs use:

```text
created → validating → ready → running → verifying → succeeded
                    ↘ rejected       ↘ failed
running → pausing → paused → resuming → running
running → cancelling → cancelled
```

Execution frames use `pending`, `ready`, `running`, `retry_wait`, `cached`,
`succeeded`, `failed`, `cancelled`, and `skipped`.

Invocations use `queued`, `acquiring_slot`, `starting`, `running`, `retrying`,
`cached`, `completed`, `failed`, and `cancelled`. The enum also reserves
`waiting_permission` and `waiting_input` so capability managers can project those states
without changing the checkpoint schema.

## Safety invariants

- No JavaScript, Python, event handlers, filesystem operations, or shell commands execute
  from Workflow HTML.
- Only compiler-whitelisted tags become instructions.
- Loops require `max-rounds`; fan-out, nesting, total Agent count, concurrency, and wall
  time are bounded.
- Side effects go through existing Agent/Tool/Skill/Connector managers and therefore keep
  their normal permission boundaries.
- Checkpoints are written atomically. Resume caches completed invocations and restarts
  incomplete ones.

## Compatibility

`schema-version` belongs to the HTML language; `runtime_version` belongs to checkpoint
files; the Workflow's own `version` tracks behavior changes. Patch-compatible 1.x HTML
may be compiled by the 1.x compiler. A runtime must reject a future incompatible major
checkpoint version rather than guessing how to restore it.

See [`docs/workflows.md`](../../../docs/workflows.md) for authoring examples.
