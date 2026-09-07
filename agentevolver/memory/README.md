---
name: memory
description: "Stores and retrieves durable context derived from agent execution history. The default implementation provides a general memory system while preserving a common Manager API."
version: 1.0.0
type: module
category: memory
requirements: []
metadata: {}
---
# Memory

Stores and retrieves durable context derived from agent execution history. The default
implementation provides a general memory system while preserving a common Manager API.

| File | Responsibility |
|---|---|
| `types.py` | Memory and configuration contracts |
| `context.py` | Memory registry and instance lifecycle |
| `server.py` | Public `memory_manager` facade |
| `default/` | Built-in memory systems |

Memory supplies relevant context; Prompt decides presentation and Agent decides when it is
used.

## Conversation versus durable notes

- Runtime gives each process a `thread_id`. A resident Agent appends each incoming task
  or event to its own Conversation, preserving its fixed prefix and compacted history.
  One-shot calls still start fresh. Step budgets reset per assignment, not per context.
- At closed step boundaries, the Agent saves a protocol-preserving snapshot through
  `P.SESSION_AGENT_CONTEXT`. `kernel.spawn(..., thread_id=previous_id, resume=True)`
  explicitly resumes it under the same bound owner/session and model route. No extra
  model call is needed. This restores dialogue, not running jobs or browser processes.
  Work interrupted after a side effect but before a closed step needs verification,
  not blind replay. Corrupt or incompatible snapshots fail visibly and are not replaced.
- `ProjectNotes` is a short, stable index over Markdown files. Bash-capable agents get
  their own directory and read/write full notes with Bash; no memory tool is mounted.
  `use_memory=False` disables this injection, not ordinary conversation continuity.
  Browser-only agents retain their private thread without gaining filesystem access.
  The index passes through `memory_manager.index` and the configured backend's
  `Memory.index(notes)`, so an evolved memory can customize ranking without replacing
  the runtime's conversation or gaining another actor's store. Existing backends
  inherit the default file index; a failed backend falls back visibly in the log.
- Paths belong to PathManager: `output/<owner>/sessions/<session>/log/memory/<actor>/notes/`.
  `P.SESSION_MEMORY_DIR` resolves the session root and `P.MEMORY_ACTOR_NOTES` resolves
  the actor's directory beneath it. Actor directory names are hashes of their identity.
  `memory_actor_id` selects a stable actor within the session; root roles default to
  their name and children to their independent context id. Reopening the same session
  restores its notes; another owner or session gets a separate store even for the same
  project. Changing the workspace does not move memory. Notes and plans are both session
  artifacts outside the deliverable, with notes under `log/memory/` and plans under `plan/`.
  `memory_project_id` no longer selects a cross-session store, and the legacy
  `AGENTEVOLVER_MEMORY_ROOT` override does not relocate current session notes.
- These are storage namespaces, not OS sandboxing for trusted host Bash. Restricted
  visitors cannot read source or memory through implicit project-context injection.
  Host-controlled benchmark containers are not given unreachable host memory paths;
  explicit per-actor mounts would be needed to enable file memory there.
- Legacy JSON, top-level `memory/`, and previous note directories are preserved, but
  not silently merged into a new actor's notes. Review and copy useful entries with Bash
  when migrating; do not import another participant's private facts. Existing HTML
  display records remain in the memory backend's directory under `log/memory/`.

## Evidence, compaction, and cost

For disk-backed threads, each compaction attempt first archives the complete closed
conversation, including opaque state and the previous checkpoint, under the thread's
`archive/` directory resolved by `P.LOG_CONTEXT_ARCHIVE`. Archives have distinct names;
the new checkpoint carries an absolute source locator. Later archives retain the
previous locator, so successive summaries do not erase the path back to older evidence.
Archive failure leaves history intact and spends no summarization call. These archives
survive thread-snapshot replacement, not deletion of the containing session/output.
In-memory calls without a bound thread have no disk-archive guarantee. Summaries remain
lossy and are not automatically proven to preserve every acceptance condition.

Notes are fallible references, never system instructions. Save reusable facts and
their source references, not every step. A model-written `seen` counter is ignored:
repeated failures need distinct event/attempt evidence and a documented correction.
The legacy Trace archive deduplicates event identities and labels shell success as
an observation, not a passing acceptance test.

ContextAssembler remains the sole active-history compaction policy. Complete tool
cycles and a user request with its recent answer stay together. Native checkpoints
from the Agent loop have `compaction_scope="history"`: fixed tasks and references
were not submitted to the compactor and must survive serialization. Legacy standalone
whole-conversation checkpoints retain their replacement semantics. Thread snapshots
persist this distinction, including on resume.
Native checkpoints
must have a readable companion before old history is replaced; failed summarization
leaves history untouched and spends a bounded attempt. Cross-route opaque-state
conversion is not implemented: explicit thread resume rejects a different model route.

The Agent loop also defaults to `compact_verify=True`: after either native or portable
summarization, the existing compact hook audits the proposed text against the full fold
source, prior checkpoint, and task. It checks semantic omissions and contradictions,
and requires exact source/checkpoint evidence quotes. Failure, invalid JSON, invented
quotes, or an inconclusive audit retains the original history. The audit uses the same
model facade and runtime budget and costs one extra call per proposed replacement;
`compact_verify=False` is an explicit opt-out, not a silent fallback. This is model-assisted
coverage checking, not proof of semantic completeness or an evaluation of opaque provider
state. Standalone memory backends are not automatically covered by this Agent-loop gate.

No note body is injected automatically or sliced to a character limit. The index may
omit complete entries with a notice and directory locator. FileSystemMemory is a bounded
recent display projection backed by Trace, not a second model context. Existing backend
registration APIs remain intact; background LLM-based memory consolidation is not enabled.

Parent execution history is shared only when the current delegation explicitly sets
`fork=true`. A parent's own fork grant does not pass to its children. Independent users
and browser acceptance workers therefore start without implicit Builder history.
Fork references include task, readable checkpoint, and complete assistant/tool groups
with full arguments and results. Old groups may be omitted with a count and a source
snapshot locator; unfinished tool cycles are excluded. Opaque provider state remains
in the original conversation, not in a user-reference block pretending to be native
replay. This is portable evidence sharing, not provider-native conversation forking.
