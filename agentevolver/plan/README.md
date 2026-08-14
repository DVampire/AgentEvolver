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

Holds a run to reading and reasoning until a person approves what it intends to do.

| Path | Responsibility |
|---|---|
| `types.py` | `PlanState` — whether the gate is closed, and what was approved to open it |
| `server.py` | `plan_manager` (per-run state) and `action_is_allowed` (the gate's rule) |

The gate itself is `agentevolver/hook/default/plan_mode.py`, which runs on
`PRE_ACTION`; the way out is `exit_plan_mode` in `agentevolver/tool/default/`.

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

- Plan mode is not in the system prompt. The model learns it is in plan mode from
  the first refusal rather than from its instructions. `PLAN_MODE_NOTICE` is
  exported for a deployment that wants to render it into the prompt.
- Only tools, skills, connectors and environments can be judged. A sub-agent
  dispatch or a workflow run is refused outright, because its effects are whatever
  the thing it runs does and no declaration can cover that.
