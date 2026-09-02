---
name: agent_loop
description: "The think-and-act loop. `Agent` declares what it is and runs `__call__ → think → act`; `ActionExecutor` runs a turn's batch; `ToolRouter` is the loop's only view of the capability system; `guards.py` holds the step middleware."
version: 1.0.0
type: module
category: agent
requirements: []
metadata: {}
---
# Agent loop

An agent is a process, and this is its main function.

| File | Responsibility |
|---|---|
| `agent.py` | The declaration and the loop: `__call__`, `think`, `act`, lifecycle hooks |
| `decision.py` | One model turn, and one action's result |
| `executor.py` | Running a turn's batch — parallel when provably safe, else serial |
| `router.py` | The loop's only view of the capability system |
| `guards.py` | Step middleware: budget, the two stall shapes, capability changes |

What is deliberately absent: prompt assembly is `agent/context`, scheduling and
messaging are `runtime`, and every per-capability special case is the router's.

## The loop

```python
for step in range(max_step):
    await proc.gate()            # safe point: signals, delivered messages
    live = await _live_blocks()  # middleware — the only place a guard lives
    emit(PRE_STEP)               # after the gate: a step that cannot run is not announced
    await _fold_if_needed(live)  # PRE_COMPACT / POST_COMPACT when history folds
    decision = await think(live) # one model call
    if decision.final: return    # no tool call means the model answered
    results = await act(decision)
    await _post_step(...)        # POST_STEP: the turn is whole
```

`PRE_STEP` sits after the middleware, not before it, because `Constraints` runs inside
the middleware and can end the run. Announced first, an exhausted budget produced a
`PRE_STEP` with no `POST_STEP`, and an observer had recorded a step that never ran.
`agentevolver/hook/README.md` draws where every event sits relative to this.

Nine lines, and every one of them is the thing it says. The class this replaces spread
the same sequence over `_advance`, `_advance_once`, `_prepare_round`, `_dispatch_round`,
`_run_one_bg`, `on_event` and `_on_round_complete`, because action results travelled back
through the agent's own mailbox instead of being awaited.

## Finishing

A turn with no tool call is the answer. No `done_tool` is required — every provider's
`end_turn` already means this, and requiring a special call to say so cost a step and
made an ordinary text reply look like a protocol violation. A capability may still
declare a run complete by returning `done` in its data, which is how an explicit finish
tool keeps working where one is mounted.

## Effects decide concurrency, not speed

`ActionExecutor` runs a batch in parallel only when *every* call is declared read-only.
Unknown counts as effectful. The cost of the two mistakes is not symmetric: running two
reads serially wastes a moment, while running two writes concurrently reorders externally
visible effects and makes an approval check meaningless.

A batch that stops early still returns one result per call. An assistant turn whose tool
calls are not all answered is unsendable, so the skipped calls come back as explicit
"not executed" results rather than as absences.

## Guards are middleware, not branches

Each guard is `async (agent, step) -> str`, and what it returns rides in that step's live
layer — past the cache breakpoint, where changing it costs nothing. Adding one is a
deliberate act with a name and a place, rather than another branch inside the loop
appending to a shared list that nothing could see whole.

| Guard | What it claims |
|---|---|
| `LandingWindow` | the remaining budget is only enough to finish |
| `NoProgress` | several turns running, every action was read-only |
| `RepeatedActions` | the identical batch was issued again, verbatim |
| `Constraints` | a declared budget is spent — the one guard that can end the run |
| `CapabilityChanges` | something was registered mid-run and the model has not been told |

The two stall guards are complementary, not redundant. `NoProgress` catches many
*different* measurements, which no repeat detector can see because every call differs;
`RepeatedActions` catches the same call again, which `NoProgress` cannot see because a
repeated write is not read-only. Neither claims to have measured progress.

This is also the only channel for advice to the model. Verbatim repetition used to be
`repeat_tool_reminder_hook` instead — a hook that subscribed to no event, was called by
name from nowhere, and was not one of the loop's two observers. It was registered,
documented as active, and never once executed. Two mechanisms for one job is how one of
them goes quiet without anyone noticing.
