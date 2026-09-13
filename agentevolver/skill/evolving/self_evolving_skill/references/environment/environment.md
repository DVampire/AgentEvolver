# Environment

This reference defines the type's artifact contract and specific checks. Follow the
[shared lifecycle](../../SKILL.md) and [conventions](../conventions.md) for inspection,
staging, registration, failure repair, evaluation, adoption and actual consumer use.

## What it is

An environment is a directory, `{extension_root}/environment/{name}/`, holding
`environment.py` (the class, exposing `@action` methods) and `ENVIRONMENT.md`. The loader wants
the directory and reads that fixed entry filename itself.

**The contract**: return a `Response`, a mapping with `success`, `message` and optional `data`,
or plain text for a successful action. The manager normalizes mappings into visible text.
Use `success=False` for failures; a JSON string containing `ok: false` is still a successful
text response to the runtime. Keep large results in artifacts and return their paths.

An environment is a stateful Python class over the shared base `Environment` that exposes named **actions** an agent can call, paired with an `ENVIRONMENT.md` manifest.

The Manager adapts calls to the shared runtime. The default is one stateful instance per
runtime owner; initialization is lazy and owner exit releases its binding. Keep application
state on the instance. For independent evaluations, choose call-scoped state and a bounded `max_concurrency`
as described below.
Do not add an environment-specific runtime or assume separate connections isolate shared files.

## Layout

An environment is a directory: `{extension_root}/environment/{name}/`
```
{name}/
├── environment.py    # REQUIRED — the Python class (registered) with @action methods
├── ENVIRONMENT.md    # REQUIRED — YAML frontmatter + body (State / Vision / Actions)
└── __init__.py       # imports the class so it registers on load
```
**Registration is a call you make**: after writing the files, call `adoption_tool` with `action="register"`, `module="environment"`, the environment name, and its directory as `artifact_path`.

**Start from the templates**: read `references/environment/template.py` (the class) and `references/environment/template-manifest.md` (the manifest), copy them, and adapt.

## Writing a new one

### Business methods and state

Adapt [template.py](template.py). Implement `@environment_manager.action` methods, optional
`initialize`/`cleanup`, and compact `get_state(ctx=None, **kwargs)`. Declare accurate effects
(`read_only`, `destructive`, `idempotent`, `open_world`) and file permissions using
`permission_op`/`permission_target` when relevant. Return failure for failed operations.

Keep mutable state on `self`. The default `state_scope="owner"` creates one instance per
Agent and orders its actions; different Agents can overlap. No manual owner map or lock is
needed. Initialize resources lazily and clean them up in the lifecycle methods. Images belong
in the action result and the manifest's Vision section.

### Independent evaluations

Use `state_scope="call"` when every request can reconstruct its inputs from arguments and
immutable artifacts. The Manager creates a fresh instance, runs the operation and cleans it
before releasing its worker slot and file claims, including on failure/cancellation. Nothing
on `self` persists to the next call; status and results must come from durable artifacts.

```python
# Fields on your Environment subclass:
state_scope: str = "call"
max_concurrency: int = 4
concurrency_group: str = "study-evaluation"  # same capacity across cooperating engines

@environment_manager.action(
    name="evaluate", read_only=False, destructive=False, open_world=False,
    read_paths=("input_path",), write_paths=("output_dir",),
    permission_op="write", permission_target="output_dir",
)
def evaluate(self, input_path: str, output_dir: str, ctx=None):
    # Implement one trial here; the Manager handles execution and lifecycle.
    # Paths arrive absolute. Read pinned inputs and write this trial's results.
    ...
```

`read_paths`/`write_paths` name path arguments, not literal paths. Manager normalizes them
against the workspace before permissions, then admits shared readers and exclusive writers.
Different output directories overlap; identical or ancestor/descendant paths serialize across
entities. These declarations coordinate access, not permission grants or a security sandbox.
Use absolute external paths or the configured workspace, not process-wide `chdir`.

One trial is one native action. Submit independent calls through the Manager; Agent batch
admission derives from `state_scope`/`concurrent`, with no duplicate `parallel_safe` flag.
The shared limit covers active native actions including initialization and cleanup. Do not
create a full worker pool inside every action or hold a permit while waiting for child calls
in the same group. Short status actions may declare `capacity_exempt=True`; never exempt
computation. Observations for call-scoped environments must stay cheap and read artifacts.

Use async methods for async I/O. A synchronous `def` runs in a framework thread and
cancellation joins it before releasing resources. This prevents event-loop blocking, but
GIL-bound calculations need a process backend for CPU speedup; hard cancellation likewise
requires an owned process, not a Python thread. Background work uses existing job facilities,
not fire-and-forget tasks. Keep restart/status behavior and per-trial error receipts explicit.

Advanced shared backends can declare `concurrent=True` and optional `resource_claims`.
Multiplexed adapters may retain `managed_sessions=True` and `close_session(owner_id)`;
ordinary environments do not need these. Shared remote state still requires its own
transaction/isolation contract. Do not infer isolation from separate connections alone.

Verify through the Manager: two owners, independent same-owner evaluations, conflicting
outputs, cancellation/cleanup, partial failure and uncached serial/concurrent parity.

### The ENVIRONMENT.md manifest

```markdown
---
name: my_environment
description: One line — what the environment is and when to use it.
version: 1.0.0
type: worker
---

<environment_my_environment>

### State

What the environment holds/simulates and how it behaves.

### Vision

(Only if actions return images.) What the visual output is and how to use it.

### Actions

#### do_thing
What it does, its arguments, and when to call it.
```

The body documents the environment's state, (optional) vision, and each action — this is what an agent reads before acting. Keep action docs concrete.

### State and input contracts

Validate and normalize configuration once when binding it. Persist effective defaults in
the versioned binding and use that same representation for every action, cache identity and
replay. Optional fields must not become required through direct dictionary access in a later
operation. Reject invalid required fields with their names before modifying state.

Stateful action descriptions must name prerequisites. Return current phase, relevant paths
and available next actions in compact state/results. Validate the phase and input files before
opening them or advancing counters; a missing authorization is a failed precondition with the
required next action, not an unexplained FileNotFoundError. Keep rejections unsuccessful and
leave state unchanged. These checks must never create missing approval/exposure records or
reset an already consumed attempt. Exercise the full valid transition sequence, omitted defaults,
an out-of-order action, identical replay and changed-input rejection in isolated state.

#### Verify and register

After writing, compile and instantiate the class with its intended configuration. Check
`get_state(ctx=...)`, action effects, successful output and a real failure response. Compilation
alone does not catch required Pydantic fields or missing effect declarations. Then register
with `adoption_tool`, `action="register"`, `module="environment"` and the directory as
`artifact_path`; inspect and exercise the returned native actions before adoption.

---

## Improving an existing one

Read `environment.py`, `ENVIRONMENT.md` and relevant initialization/state code. Preserve
`@ENVIRONMENT.register_module`, the registered name and compatible action signatures. Keep
Actions, State and Vision documentation aligned with actual behavior and effects. Reproduce
the failing state transition in isolated state, then use the common repair loop. Registration
creates a fresh instance for subsequent native calls, with package imports isolated by admitted
revision. It does not migrate live state or rewrite references held by other consumers. Inspect
the active binding/version, initialize resources when needed, and execute the repaired behavior
through native calls before attributing results to the candidate. Persist or explicitly hand off
required state; do not mistake successful registration for a verified upgrade.

## Evaluating one

Call `inspect_tool` (`capability_type="environment"`) on the target for its registry facts (registered / enable_evolving / version / file paths). Check the type-specific requirements:
1. **Interface Compliance** — `@ENVIRONMENT.register_module`, subclass `Environment`, actions declared with `@environment_manager.action`, `initialize`/`cleanup` present where resources are used.
2. **Code Quality** — valid, clean, proper resource handling and error handling; owner-bound state or correctly keyed multiplexed sessions.
3. **Manifest Quality** — ENVIRONMENT.md has the required frontmatter and a body documenting State / (Vision) / every Action.
4. **Integration** — `inspect_tool` (`capability_type="environment"`) shows it registered.
5. **Execution** — initialize the candidate, run representative native actions and inspect
   resulting state/observations. Test a valid operation, a failed operation and a relevant
   state transition/reuse case. Verify cleanup, reset and isolation where applicable;
   a permanently blocked stub does not implement the declared operation.

---
