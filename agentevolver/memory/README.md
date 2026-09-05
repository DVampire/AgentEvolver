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
- Paths belong to PathManager: `memory/<owner>/<project>/actors/<actor>/`, outside
  disposable `output/`. Configure `memory_project_id` (or the same context extra) for
  a stable project across temporary checkouts; `memory_actor_id` selects a stable actor.
  Root roles default to their name; children default to their independent context id.
  Without an explicit project id, workspace/source identity preserves case isolation.
  `AGENTEVOLVER_MEMORY_ROOT` can place notes on a durable volume.
- These are storage namespaces, not OS sandboxing for trusted host Bash. Restricted
  visitors cannot read source or memory through implicit project-context injection.
  Host-controlled benchmark containers are not given unreachable host memory paths;
  explicit per-actor mounts would be needed to enable file memory there.
- Legacy JSON and old `output/.../memory` notes are preserved, but not silently merged
  into a new actor's private notes. Review and copy useful entries with Bash when
  migrating; do not import another participant's private facts.

## Evidence, compaction, and cost

Notes are fallible references, never system instructions. Save reusable facts and
their source references, not every step. A model-written `seen` counter is ignored:
repeated failures need distinct event/attempt evidence and a documented correction.
The legacy Trace archive deduplicates event identities and labels shell success as
an observation, not a passing acceptance test.

ContextAssembler remains the sole active-history compaction policy. Complete tool
cycles and a user request with its recent answer stay together. Native checkpoints
must have a readable companion before old history is replaced; failed summarization
leaves history untouched and spends a bounded attempt. Cross-route opaque-state
conversion is not implemented: explicit thread resume rejects a different model route.

No note body is injected automatically or sliced to a character limit. The index may
omit complete entries with a notice and directory locator. FileSystemMemory is a bounded
recent display projection backed by Trace, not a second model context. Existing backend
registration APIs remain intact; background LLM-based memory consolidation is not enabled.
