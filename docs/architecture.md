# AgentEvolver — Runtime & Agent Architecture

How agents run and talk to each other. Three layers, bottom-up:

```
┌──────────────────────────────────────────────────────────────┐
│  agents / tools / hooks                                        │
├──────────────────────────────────────────────────────────────┤
│  protocol/   channels — typed agent-to-agent conversations     │
│              (escalation · delegation · progress · control ·   │
│               query · pubsub)                                  │
├──────────────────────────────────────────────────────────────┤
│  runtime/    actor kernel — mailbox + pump + transport verbs   │
│              (send · ask · suspend/resume · publish/subscribe) │
└──────────────────────────────────────────────────────────────┘
```

`runtime` = *how messages move*. `protocol` = *the shape of each conversation*. Agents are
plain `Agent` instances; orchestration is a matter of roster + a couple of seams, not a
separate agent type.

---

## 1. Runtime — the actor kernel (`src/runtime/`)

Every running agent is an **actor**: one `AgentRef` with an inbox (`asyncio.Queue`), driven
by a **pump** that drains the inbox and dispatches each message to `agent.handle(msg, ref)`.

`RuntimeManager` (`src/runtime/server.py`, singleton `runtime_manager`) owns the running
refs and exposes the transport verbs any protocol builds on:

| Verb | Pattern | Meaning |
|------|---------|---------|
| `send(ref, msg)` | tell | fire-and-forget into a ref's inbox |
| `ask(ref, msg, timeout)` | request-reply | send + await the message's `reply_future` |
| `suspend(key, timeout)` / `resume(key, value)` | **gate / rendezvous** | one coroutine blocks on a key; a *different* one resumes it by key |
| `publish(topic, msg)` / `subscribe` / `unsubscribe` | fan-out | broadcast to every running subscriber of a topic |
| `spawn` / `invoke` / `stop` | lifecycle | start a pump / one-shot run / stop |

`invoke(agent, task=…, ctx=…)` = spawn a pump, `ask` a `TaskMessage`, await the result,
stop. This is how one agent runs another.

The runtime knows nothing about "escalation" or "delegation" — those are protocols.

---

## 2. The unified event-driven agent loop (`src/agent/types.py`)

**Every agent — leaf actor AND orchestrator — runs the same loop.** There is no special
"meta runtime". The loop lives in the base `Agent`:

```
on_start(task, ref)                 # init a run, emit ON_START hooks, kick the first turn
  └─ _advance(run)                  # ONE turn:
        constraint check → _get_messages → _think → _prepare_round → _dispatch_round
  _think(msgs)                      # one LLM turn → {tool_calls, routing, reasoning, tokens}
  _dispatch_round(run, calls)       # spawn each call as a bg task → _run_one_bg → inbox
on_event(msg)                       # an action finished / an escalation arrived
  └─ when the round drains → _on_round_complete → _advance (next turn) or _conclude
_conclude(run)                      # cancel stragglers, ON_STOP hooks, resolve the caller,
                                    #   _finalize_run, on_end
```

- **round == turn.** The batch of tool_calls the model emits in one turn runs concurrently;
  the next turn starts once that batch drains. Sequential work = across turns (the model
  decides by how it batches — function-calling's own contract).
- **`_run_one` / `_invoke_capability`** dispatch a single call to its owning manager. A
  **sub-agent is just another capability** (`kind == "agent"` → `protocol_manager.delegate`),
  so an orchestrator uses the exact same dispatch path as a leaf calling `bash_tool`.
- **`__call__`** is a thin `runtime_manager.invoke(self)` wrapper — the synchronous entry
  runs the same event-driven loop under the pump.

### Orchestrator = base loop + seams

`MetaAgent` (`src/agent/actor/meta_agent.py`) is a normal `Agent` (~130 lines, two real
overrides). It differs only through base-class **seams**:

| Seam | Leaf default | MetaAgent |
|------|--------------|-----------|
| `_include_agents()` | `False` | `True` — registered sub-agents are projected into the roster as callable tools |
| `_handle_extra_event(run, msg)` | ignore | on an escalation: a focused think turn → reply to the blocked sub-agent |
| `_get_messages` | standard prompt | (uses base modules; orchestration guidance is in the `meta_agent` template) |
| `_prepare_round` / `_finalize_run` / `on_end` | pass-through | optional policy hooks |

`BrowserAgent` keeps a bespoke loop and overrides `on_start` to run its own `__call__`;
generate/optimize agents put their post-run registration in `_finalize_run` (runs on every
path, not just direct calls).

### Native tool use (`src/agent/native_tools.py` + each manager)

The model sees every capability as a flat `tools` list. Each **manager owns its own
projection** — `tool_manager.function_callings(...)`, `skill_manager…`, `connector_manager…`,
`agent_manager…` — returning `(schema, route)` pairs. `assemble_native_tools` only
concatenates them and builds the routing table. Names are the entities' **own registered
names** (already type-marked by suffix: `*_tool` / `*_agent` / `*_skill`), never re-prefixed.
Completion is the ordinary registered `done_tool`.

---

## 3. Protocol — typed channels (`src/protocol/`)

`ProtocolManager` (singleton `protocol_manager`, `src/protocol/server.py`) is the set of
conversations agents have, each built on a runtime verb. Message types live in
`src/protocol/types.py`.

| Channel | Pattern | Method(s) | Used for |
|---------|---------|-----------|----------|
| **escalation** | gate | `escalate(ctx, …)` / `reply(task_id, …)` | a blocked sub-agent asks its parent, resumes on the reply |
| **delegation** | ask | `delegate(child, task, parent_ref=…)` | run another agent and get its Response — meta→sub AND meta1→meta2 |
| **progress** | tell | `report(parent_ref, msg)` | stream status to the parent |
| **control** | tell | `cancel` / `pause` / `resume` | steer a running agent (handled in its `on_event`) |
| **query** | ask | `query(ref)` | ask a running agent for a status snapshot |
| **pubsub** | fan-out | `subscribe` / `publish` | broadcast an event to a topic |

### Escalation end-to-end (the reference flow)

```
sub-agent blocked
  → escalate_tool                     (the LLM's trigger)
    → protocol_manager.escalate(ctx)  (send EscalationMessage to parent inbox)
      → runtime.suspend(task_id)      (sub-agent's coroutine parks on its task_id)
parent (e.g. MetaAgent) inbox
  → on_event → _handle_extra_event    (a focused think turn decides guidance)
    → protocol_manager.reply(task_id, guidance)
      → runtime.resume(task_id, …)    (sub-agent unblocks, continues)
```

Because *every* agent runs the event-driven loop, the parent processes the escalation on
its pump while the round is still in flight — no deadlock. `escalate_tool`/`reply_tool` are
the LLM-facing triggers; `protocol_manager` is the channel; `runtime.suspend/resume` is the
transport. (`ask_question_tool` was folded into `escalate_tool`.)

> Lineage note: a sub-agent's `AgentContext.parent_session_id` is what lets `escalate` find
> the parent. `BaseContext.from_context` preserves it (and `subtask_id`) through the
> `AgentContext → ToolContext` conversion so tools can still see who dispatched them.

---

## 4. Model providers (`src/model/`)

Four providers behind one `model_manager`: **openai**, **openrouter** (OpenAI-compatible,
also fronts Gemini/Claude via OpenRouter), **anthropic** (`anthropic` SDK), **google**
(`google-genai` — the unified SDK; the legacy `google-generativeai` was deprecated
2025-11-30). Each provider implements the same contract in its `chat.py`:
`_build_params` → `_call_model` / `_open_stream` → `_parse_stream` / `_format_response`.

Structured output is **derived from whether tools are present**: no tools → native
`response_format`/`response_schema`; with tools → the schema rides along as a synthetic tool
(native structured output and native tool calling can't coexist) and is folded back into
`parsed_model`. Google thinking is configured via `thinking_config`; thought summaries are
routed to the thinking channel, not the answer.
