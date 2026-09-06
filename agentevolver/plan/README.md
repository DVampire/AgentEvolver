---
name: plan
description: "Holds a run to reading and reasoning until a person approves what it intends to do, so an approach is agreed before it is carried out rather than after."
version: 1.0.0
type: module
category: infrastructure
requirements: []
metadata: {}
---
# Plan

Maintains coordinator plans and, when requested, holds execution for human review.

| Path | Responsibility |
|---|---|
| `types.py` | `PlanState` — whether the gate is closed, and what was approved to open it |
| `server.py` | `plan_manager` (per-run state) and `action_is_allowed` (the gate's rule) |

The gate itself is `agentevolver/hook/default/plan_mode.py`, which runs on
`PRE_ACTION`; the way out is `exit_plan_mode` in `agentevolver/tool/default/`.

## Coordinator planning

`MetaAgent` and `WebsiteBuilderAgent` enable `use_plan` by default. Code, browser,
website-user and other leaf workers leave it disabled. A worker receives a bounded
assignment and returns evidence; its coordinator owns planning and replanning.

`PlanManagerServer.context()` reads the session's `workspace/plan.md` each step.
The agent loop adds it to the volatile context layer after the cached conversation,
so revisions appear immediately and survive history compaction. The plan is the
coordinator's working document, not a separate model or an execution engine.

For a host controller using Bash in a task container, the launcher declares the host
mount source with `AGENTEVOLVER_TASK_WORKSPACE` and its container target with
`AGENTEVOLVER_EXEC_WORKDIR`. The plan context shows the container path (normally
`/workspace/plan.md`); the plan manager reads and approves the same file through its
host path. `path_manager.execution_path()` only translates paths under this declared
mount, so other sessions' paths are never advertised as local container files.

- `auto`: maintain the plan before multi-step work and revise it before acting on
  new feedback. The agent writes it with its normal workspace tools. No approval gate.
- `plan`: the existing effect gate waits for human approval through `exit_plan_mode`.
  Approval writes the plan to disk. Keep that tool mounted when using this mode.
- `off`: omit the planning context and automatic planning obligation.

Meta and website demo launchers default to `auto`. This is a planning instruction,
not a semantic guarantee that every suggestion was implemented. A useful plan records
goals, constraints, design choices, step status and acceptance checks. Each feedback
round records source/participant, turn and release, observed needs versus proposals,
accepted/deferred changes and reasons, the next experiment, and its evidence.
Implementation, technical verification and user confirmation remain separate states.
Workers do not maintain copies of this document.

When `use_plan` and the shared evolution policy are enabled, the live planning context also
requires an **Evolution opportunities** section near the top of `plan.md`. This applies
to Meta and Builder through the shared agent loop; it adds no automatic plan obligation
to leaf workers and is omitted in `off` mode. An explicitly active review gate still
applies: describing an experiment does not authorize executing it before approval.
Policy availability comes from the system prompt's scoped capability roster and
permissions; `enable_evolving` describes target mutability, not this permission.

Each concrete opportunity has a stable ID, source evidence, reusable operation or
method, intended consumer/next use, expected benefit, inspected capabilities or next
discovery step, smallest experiment and baseline/reuse checks, and a status/next action
with rationale. Replanning preserves unresolved entries. Repeated deferral requires a
fresh cost assessment against actual resources and intended reuse, with a constraint
and revisit condition. A qualifying experiment becomes an ordered plan step. Its entry
then records the exact candidate, evaluation evidence, adoption decision and subsequent
consumer use separately. No qualifying opportunity is a valid recorded assessment.

Self-verification is an explicit discovery boundary: after meaningful local tests,
debugging, browser checks or final review, separate the product/setup problem from what
the result teaches about the agent's implementation or verification method. A passing
check or a first correction can justify a bounded experiment; a different check or operation
in the same project can be its next consumer. Link the observation, method change and
comparison in the opportunity entry, and revisit an early “none identified” assessment
when new evidence arrives. Benchmark discovery uses solver-visible inputs and local
checks; hidden grading stays outside this loop. A manual diagnosis or a candidate's local
evaluation is not an official benchmark result.

For example, a browser result that serializes an entire scene may justify investigating
a bounded observation operation: cite the failed call, identify the next browser check
as consumer, compare useful diagnostic coverage and output size, and test an independent
page before adoption. This is an illustration, not a required capability or a prefilled
plan. The coordinator authors the assessment from its run's evidence. Runtime projects
the instructions and latest document; it does not semantically validate these decisions,
force an evolution quota, or treat a written opportunity as verified evolution.

Website Builder also projects current-release feedback status every step. Deployment
receipts carry `subscriber_min_turns`, and `job__wait(min_turns_by_job=...)` waits for
each subscriber's own execution count. A retry does not renumber the product release.
Read each returned completed turn in full with `job__output`, without `tail`.
The first full read records the current plan's content hash. If the plan is unchanged
on subsequent steps, the Builder sees an explicit reminder to replan before edits.
This detects an unchanged document; it does not grade the plan or certify user satisfaction.

Keep the current plan concise. Detailed feedback and historical evidence can live in
linked files. The live projection is bounded to 16,000 characters and explicitly asks
the coordinator to read the complete file when that limit is exceeded.


## Why it exists

Review after the fact is not review. An agent that has already rewritten six files
presents a person with a diff and a sunk cost, and the only cheap answer is yes. The
expensive disagreements — wrong approach, wrong file, wrong assumption about what
the task meant — are all visible in the plan and all invisible in the diff.

So plan mode moves the decision earlier: the agent explores, says what it means to
do, and cannot do it until someone says go.

## What it is not

Not a permission system and not a sandbox. Plan mode is one flag with one rule, set
by a person for a particular stretch of work. Anything that must hold whatever the
agent or the person does — network policy, filesystem confinement, credentials —
belongs in the sandbox and the permission mode, which do not read this state.

Not persistent. A run in plan mode that ends is not in plan mode when it is
restored; the flag is a stance toward work in progress.

## The contract

- **The gate reads declarations, never names.** An action runs while plan mode is
  active if its capability declared `mutates: False` or `permission_mode:
  "read_only"`. `action_is_allowed` never sees a name except to check the small
  always-allowed set, because a name is not a behaviour — the mistake that
  `hook/default/repeat_tool.py` documents at length.

- **Silence is a refusal.** A capability that declared neither field is blocked.
  `bash_tool` is exactly that capability, and reading "nothing declared" as "safe"
  would let the one tool that can do anything through the gate that exists to hold
  it. The cost is real: a read-only capability that never declared itself is
  blocked too, and the fix is for it to declare.

- **Something is always legal.** `exit_plan_mode`, `ask_user_question` and
  `done_tool` run whatever they declare. Between them the agent can always propose,
  ask, or stop; a gate with no legal move produces an agent that burns its budget
  discovering that.

- **A block is explained to the model, not just logged.** The refusal carries
  `PLAN_MODE_NOTICE`, which names `exit_plan_mode` as the way out. A refusal that
  does not say how to stop being refused produces the same call, again.

- **Approval is a person's, and only for this plan.** `approve()` records the plan
  verbatim; `enter()` clears any previous approval, so a second round of work cannot
  inherit consent given for the first. `leave()` opens the gate with no approval
  recorded, so a cancelled plan mode never reads as an agreed plan.

## Known gaps

- Only tools, skills, connectors and environments can be judged. A sub-agent
  dispatch or a workflow run is refused outright, because its effects are whatever
  the thing it runs does and no declaration can cover that.
