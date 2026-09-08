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
| `recent` | exact assistant/tool turns, delivered events, changed plan observations | appended to |
| `live` | budgets, errors, environment state, reminders | every step |

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

`Agent.compact_input_tokens` optionally triggers on full input, including fixed prompts,
tool schemas, images and cached tokens, excluding output. The website demo sets it to
50,000. Before generation, the assembler checks the current request estimate calibrated
by that agent's latest provider-reported input / local estimate ratio (never below 1).
This is an early compaction trigger, not an exact provider token cap: a new large tool
result or an estimator error can overshoot it. Only closed turns can fold, recent turns
stay intact, and a failed semantic audit retains the original history. The body and
context-window triggers remain available; cumulative execution budgets are separate.
The compact hook reserves completion headroom for reasoning separately from the readable
checkpoint size. A structured semantic rejection permits one repair using the original
source and audit findings; the corrected candidate must pass the same size and semantic
checks. A second rejection or an unavailable audit keeps the original history.
The audit considers the unchanged task, latest observations and retained tail alongside
the candidate, so a requirement kept verbatim need not be duplicated in the summary.

Delivered events are saved immediately in the conversation, deduplicated by envelope ID,
and included in the source of later compaction. A plan observation is appended only when
its content or mode changes. The latest observation is retained verbatim across folds
and resume; its saved index points into history rather than duplicating the document.
Stable planning rules come from PlanManager once in the fixed layer.

Capability discovery uses the existing catalog and `search_capabilities` route. Besides
the count threshold, `Agent.capability_schema_tokens` is a soft initial schema budget
(estimated 8,000 tokens; 0 disables it). Core and explicitly discovered capabilities
remain visible even above that budget; scope checks still apply. Batch programs use the
current native schemas instead of a duplicate fixed SDK.
