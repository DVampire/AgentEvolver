---
name: agent_context
description: "The context window: the held conversation, the validated four-layer envelope, cache placement and folding. `ContextAssembler` builds a request from held history; `ContextBuilder` projects one from a persisted trace."
version: 1.0.0
type: module
category: agent
requirements: []
metadata: {}
---
# Agent context

What an agent sees, in what order, and what it costs. One responsibility per file.

| File | Responsibility |
|---|---|
| `layers.py` | The four layers, and per-layer token accounting |
| `envelope.py` | The validated fixed → checkpoint → recent → live envelope |
| `conversation.py` | The held history of one run |
| `assembler.py` | Layout, cache breakpoints and folding, from a held conversation |
| `builder.py` | The same envelope, projected from a persisted trace |
| `sanitize.py` | Stripping author-only template comments |
| `project.py` | CLAUDE.md / MEMORY.md / AGENTS.md in the fixed layer |
| `errors.py` | The protocol error every module above may raise |

"Context" means two things here and both are kept, in separate files. `manager.py` is the
agent registry, so `from agentevolver.agent.context import AgentContextManager` resolves
exactly as `model/context.py` and `tool/context.py` do for theirs. Everything else is the
window a request fills. Seven hundred lines of registration once shared one *file* with
prompt assembly; separating the files is most of what this split was for.

## Agents ask for a context; they do not build one

Prompt layout used to live on the agent base class across twenty-one methods, so any
actor that needed one different block overrode an assembly method and quietly acquired a
different layout. Here there is one class that decides, and agents pass it their history
plus this step's volatile blocks.

## The four layers are a cache strategy

A provider's prompt cache is a **prefix** match, so where a block sits decides whether a
session pays for its own history every step.

| Layer | Holds | Rewritten |
|---|---|---|
| `fixed` | system prompt, task anchor | never |
| `checkpoint` | the one canonical fold summary | only when history folds again |
| `recent` | exact assistant/tool turns and delivered user/runtime events | appended to |
| `live` | current plan Brief/path, budgets, errors, environment state, reminders | every step |

Breakpoints go after `fixed`, after `checkpoint`, and after the last assistant message
in `recent` — three, against Anthropic's limit of four. Nothing volatile is ever placed
earlier, because one volatile block near the front throws away everything behind it.

## Validation is the point

`ContextEnvelope.validate()` refuses a context that looks fine and is subtly wrong: an
assistant turn whose tool results never arrived, a compaction summary in the wrong layer,
one message claimed by two layers, a tool-call id reused in the tail. Each of those is
otherwise a provider rejection on the next request, far from the code that built it.

## Folding never rewrites

`Conversation.fold` replaces everything before the retained tail with one checkpoint
message, and the cut always lands on an assistant turn so the kept tail never opens with
an orphan tool result. The summary supersedes what was said rather than editing it —
which is also what keeps the prefix behind the fold point stable for the cache.

Writing the summary needs a model, and this module does not own one: the assembler says
*when* to fold and *what* to summarise, and the agent supplies the text.

The full plan stays in `plan.md`. Only its bounded `## Brief` progress index and path
are projected into `live`; neither current nor previous automatic plan projections
enter history compaction. User feedback and explicit tool reads remain ordinary history.
An explicit resume migrates legacy tagged plan snapshots out of the held history and
standalone Responses checkpoint messages without rewriting the on-disk source archive.

## Compaction checks and recovery

The fold measures the complete request before and after replacement, including schemas,
the checkpoint and live data. Recent-history savings remain a separate trace field;
they cannot establish success when the new checkpoint grew. Trace events record the
step, full input estimates, reclaimed input, headroom target and retry conditions.

The target is 75% of the lower of the configured input trigger and input capacity.
Before summarising, the retained tail may shrink from the configured maximum (normally
four turns) to one complete turn to leave room. Every removed turn is included in the
summary source, and call/result pairs stay together. A large last turn or fixed prefix
can make this target unattainable; it is a target, not permission to truncate content.

If native state still exceeds the target, compare its already-produced readable companion
as a portable checkpoint. Use it only when smaller, without an additional summary call.
If even that candidate does not shrink the complete request, retain the original exact
history and report the candidate size separately; a completed summary is not proof of savings.
Native programs unsupported by the Responses compact endpoint go directly to portable
summarisation, preserving program code/results but excluding encrypted reasoning and image
bytes. A readable summary is still required before replacing history; failures keep the
original conversation. Budget exhaustion and cancellation propagate normally.

When a fold fails, does not shrink the full request, or cannot leave headroom, scheduled
folding waits at least two steps (normally the configured retained-turn count). An applied
fold without headroom also requires new input growth of at least the greater of four
summary budgets and 10% of the trigger before trying again. Capacity pressure and a real
provider overflow bypass this cost backoff. The task's cumulative token/step limits remain
independent of these thresholds.

For llm_hub Responses, request accounting uses the route's message serializer so canonical
text and native replay state are not charged twice. Images have a separate estimate rather
than charging their base64 bytes as text. Schemas, opaque checkpoint bytes and tokenisation
are still estimates; provider-reported usage is authoritative. The portable summariser
does not receive another copy of a task already retained in the fixed layer.

`Agent.compact_input_tokens` defaults to 100,000 full input tokens, including fixed prompts,
tool schemas, images and cached tokens, excluding output. The website and game demos set
100,000; a launch override can change it. Before generation, the assembler checks the current
request estimate calibrated by that agent's latest provider-reported input / local estimate
ratio (never below 1).
This is an early compaction trigger, not an exact provider token cap: a new large tool
result or an estimator error can overshoot it. Only closed turns can fold, recent turns
stay intact (up to four by default), and system/task anchors are never summarized away. A successful
summary replaces the folded history without a semantic audit, repair call or length rejection.
If generation fails, the original history remains. The checkpoint length is a soft generation
target, with separate completion headroom for reasoning.

Turn-count and body-only triggers are opt-in (both default to 0); the context-capacity trigger
remains active at 85% of the configured window. Setting `compact_input_tokens=0` disables only
the full-input trigger. Cumulative execution budgets are separate from these context controls.

Delivered events are saved immediately in the conversation, deduplicated by envelope ID,
and included in the source of later compaction. The plan Brief is read fresh into the
live layer after each boundary; it is never appended as a historical plan observation.
Stable planning rules come from PlanManager once in the fixed layer.

Capability discovery uses the existing catalog and `search_capabilities` route. Besides
the count threshold, `Agent.capability_schema_tokens` is a soft initial schema budget
(estimated 8,000 tokens; 0 disables it). Core and explicitly discovered capabilities
remain visible even above that budget; scope checks still apply. Batch programs use the
current native schemas instead of a duplicate fixed SDK.
