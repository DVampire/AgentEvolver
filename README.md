<div align="center">

# AgentEvolver

**A self-evolving multi-agent framework.**

A **MetaAgent** orchestrates sub-agents to finish your task — while generator / evaluator / optimizer
agents write, score, and rewrite the framework's own tools, skills, agents, connectors, environments,
workflows and memory systems underneath it.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![Quality](https://github.com/DVampire/AgentEvolver/actions/workflows/quality.yml/badge.svg)](.github/workflows/quality.yml)
[![Docs](https://img.shields.io/badge/docs-workflows%20%C2%B7%20canvas%20%C2%B7%20schemas-8A2BE2.svg)](docs/)

[Quick start](#-quick-start) · [Highlights](#-highlights) · [Architecture](#-architecture) · [Web UI](#-the-web-ui) · [Sandbox & safety](#-sandbox--safety) · [Extending](#-extending-the-framework)

🌐 中文版请见 **[README_zh.md](README_zh.md)**

<img src="docs/assets/arch.png" alt="Self-Evolving Agent System Architecture" width="100%">

</div>

---

## 📌 At a glance

| | |
|---|---|
| **48** framework modules, ~**91k** lines of Python | each with its own versioned `README.md` contract |
| **29** built-in agents | 8 actors + 7 generators + 7 evaluators + 7 optimizers |
| **27** tools · **83** skills · **25** MCP connectors | plus **89** service plugins exposing **332** tools |
| **4** execution environments | browser · Linux desktop · SSH · artifact renderer |
| **6** sandbox backends | host · docker · playwright · chrome-vnc · desktop · vscode |
| **84** Gateway commands | one versioned, replayable protocol for every client |
| **9** benchmarks · **41** test modules | AIME · GPQA · GSM8K · HLE · LeetCode · DeepWeb · ProgramBench |

---

## ✨ Highlights

<table>
<tr>
<td width="50%" valign="top">

### 🧬 It rewrites itself
A closed loop — **detect gap → generate → evaluate → adopt or roll back** — runs *while* serving your
task, strictly separated from the user work itself. Every version is archived; any of them can be
restored with one call.
→ [details](#the-self-evolution-loop)

</td>
<td width="50%" valign="top">

### 📄 Everything is HTML you can open
Prompts, workflows, task documents, memory reports and per-step snapshots are all **complete HTML
documents** — executable by the runtime *and* browsable, diffable, reviewable by a human.
→ [details](#html-native-everything)

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔒 The sandbox has no way out
Task containers get **no network interface** — only loopback. The single route out is a host-side
relay over a Unix socket, so egress policy lives where the sandboxed process cannot edit it, and
**every attempt is recorded**.
→ [details](#-sandbox--safety)

</td>
<td width="50%" valign="top">

### ⏱ Budgets the model can see
Step / token / wall-time constraints aren't just kill-switches: the remaining budget is rendered
into the prompt every step as **NORMAL / TIGHT / CRITICAL** with a policy hint, so the agent plans
around it instead of being cut off.
→ [details](#constraints-budgets-the-agent-can-read)

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🖥 Four views, one project
Chat, a visual **flow canvas**, real **VS Code in the browser**, and a **Science workstation** with a
live Jupyter kernel — all editing the same workspace, over one WebSocket protocol.
→ [details](#-the-web-ui)

</td>
<td width="50%" valign="top">

### 🗂 One table owns the disk
Every path the framework writes is declared in a single enum-keyed table. Two writable roots, and
only two — a rule that's enforced by a test, not by convention.
→ [details](#-output--project-layout)

</td>
</tr>
</table>

---

## 🚀 Quick start

### 1. Install

```bash
bash scripts/install.sh
```

Creates a conda environment (`agentos`, Python 3.12), installs the package and its dependencies,
installs Node.js, and writes an `.env` template. Re-running is safe. Add `--extras browser` for
browser automation, `--uv` to use uv instead of conda, or `--help` for all options.

Then put your API keys in `.env` at the project root:

```bash
ANTHROPIC_API_BASE='...'
ANTHROPIC_API_KEY='...'
OPENROUTER_API_BASE='...'
OPENROUTER_API_KEY='...'
```

Keys can instead be managed centrally in **Vault**, which the framework prefers whenever it is
configured and reachable, falling back to `.env` otherwise.

> Full details — manual setup, Vault, and optional extras: **➡️ [scripts/INSTALL.md](scripts/INSTALL.md)**

### 2. Build the sandbox image (once)

AgentEvolver runs the **entire framework inside a container** (this is "Model X"): the MetaAgent,
every sub-agent, and all tool execution (bash / file edits / git / experiment code). The host only
launches it; the repo is bind-mounted in, so source edits are live and outputs land back on the host
under `output/`. Service peers (browser, task images) are spawned as sibling containers through the
mounted Docker socket over the shared host network.

```bash
docker build -f docker/base/Dockerfile -t agentevolver/base:latest .
```

From then on **everything runs the same way** — hand a command to the launcher after `--`:

```bash
scripts/run-in-sandbox.sh -- <command>          # run <command> in the sandbox
scripts/run-in-sandbox.sh --gpus -- <command>   # ...also exposing NVIDIA GPUs
```

The launcher needs a reachable Docker daemon and refuses to fall back to the host; use `--image IMG`
for a different base image. The bare `<command>` also runs directly on the host (no container) after
`conda activate agentos`, handy for quick local development.

### 3. The three things you'll run

<details open>
<summary><b>① Run a task (MetaAgent)</b></summary>

[`examples/run_meta_agent.py`](examples/run_meta_agent.py) boots the MetaAgent with its sub-agents and
runs a single task to completion.

```bash
# Default task
scripts/run-in-sandbox.sh -- python examples/run_meta_agent.py

# Inline task
scripts/run-in-sandbox.sh -- python examples/run_meta_agent.py \
  --task "Write a Python function to reverse a string and add unit tests."

# Task from a document (.html / .md under examples/tasks/)
scripts/run-in-sandbox.sh -- python examples/run_meta_agent.py \
  --task-file examples/tasks/qsar_egfr_experiment.html
```

| Flag | Description |
| --- | --- |
| `--task "<text>"` | Inline task string. Takes priority over `--task-file`. |
| `--task-file <path>` | Path to a task document (`.html` / `.md`) under `examples/tasks/`. |
| `--config <path>` | Config file (default: `configs/meta_agent.py`). |
| `--cfg-options key=value ...` | Override any config field, e.g. `--cfg-options model_name=openai/o3`. |

Each run is its own session: artifacts, logs, and task views land under
`output/<owner>/sessions/<session-id>/` (`workspace/` for the agent's files, `log/` for logs and
rendered task views). On completion the log prints the final result and, if produced, the path to a
memory HTML report. Ready-made task documents live in [`examples/tasks/`](examples/tasks/), and
[`examples/`](examples/) also has a `run_*.py` for every individual agent.

</details>

<details open>
<summary><b>② Interactive web UI</b></summary>

[`frontend/`](frontend/) is a React/Vite browser UI that talks to the Python runtime over the
versioned Gateway protocol. Under Model X **both the backend Gateway and the frontend dev server run
inside the sandbox** — one container, two processes, started together by
[`scripts/serve-ui.sh`](scripts/serve-ui.sh):

```bash
scripts/run-in-sandbox.sh -- scripts/serve-ui.sh
```

Then open `http://127.0.0.1:5173` in the host browser (the Vite dev server); it connects to
`ws://127.0.0.1:9876/ws` by default. The sandbox uses `--network host`, so both ports are reachable
from the host with no extra setup. The first launch runs `npm install` inside the sandbox (deps land
in `frontend/node_modules`; later launches skip it). Override ports with `GATEWAY_PORT` / `UI_PORT`;
args after the script pass through to `agentevolver serve` (e.g. `--token`, `--allow-origin`).

Set `AGENTEVOLVER_GATEWAY_TOKEN` before binding the Gateway outside a trusted local network — it is
required for non-loopback hosts, and browser origins can be restricted with repeated
`--allow-origin`. See [`frontend/README.md`](frontend/README.md) for the full guide.

</details>

<details open>
<summary><b>③ Run the tests</b></summary>

```bash
scripts/run-in-sandbox.sh -- pytest -q                        # fast suite
scripts/run-in-sandbox.sh -- pytest -q tests/test_gateway.py  # one file
scripts/run-in-sandbox.sh -- pytest -m integration            # needs creds / services / peers
```

Tests live in [`tests/`](tests/). The default run passes `-m 'not integration'` (see
`pyproject.toml`), so it needs no API keys or Docker peers and finishes quickly.
`scripts/install.sh` already runs this suite once as a post-install check. CI additionally enforces
that every module's `README.md` contract stays in sync (`tests/test_module_readmes.py`) and that the
disk layout keeps exactly two writable roots (`tests/test_paths.py`).

</details>

---

## 🏗 Architecture

```
                       ┌──────────────────────────────────────────────┐
   user / browser ───▶ │  Gateway  (84 commands, versioned, replayable)│
                       └───────────────────────┬──────────────────────┘
                                               ▼
                       ┌──────────────────────────────────────────────┐
                       │  MetaAgent — plan · decompose · dispatch      │
                       └───────────────────────┬──────────────────────┘
                    Runtime (mailboxes, pump)  │  Protocol (typed channels)
                                               ▼
   ┌──────────────────────── Evolvable capability ecosystem ────────────────────────┐
   │  agents+prompts · tools · skills · connectors · environments · workflows       │
   │  memory systems · plugins · knowledge (RAG) · process transforms               │
   └───────────────────────────────────┬────────────────────────────────────────────┘
                                       ▼
   ┌──────────── Infrastructure ────────────┐   ┌──────── Self-evolution ────────┐
   │ sandbox · permission · constraint      │   │ generate → evaluate → optimize │
   │ trace · trajectory · paths · ports     │◀─▶│ ExtensionManager + versioning  │
   │ model · config · version · benchmark   │   │ journal · smoke gate · rollback│
   └────────────────────────────────────────┘   └────────────────────────────────┘
```

Most component modules share one shape: `default/` (built-ins) + `types.py` (base class + contracts)
+ `context.py` (registry & lifecycle) + `server.py` (the `*_manager` singleton facade). Learn one
module and you know the rest.

### The self-evolution loop

The framework evolves its **own** capabilities while serving a user task — kept strictly separate
from *user work* (done by actor agents like `code_agent` / `general_agent`).

```
  decide            generate / optimize            evaluate              adopt or roll back
  ───────           ───────────────────            ────────              ──────────────────
  a capability  →   *_generate_agent writes    →   *_evaluate_agent  →   promote, or restore
  is missing or     a new component, or            scores whether        any archived version
  too weak          *_optimize_agent edits         it actually helped
                    an existing one's source
```

| Piece | What it does |
|---|---|
| **7 generators** | one `*_generate_agent` per type: tool, agent(+prompt), skill, connector, environment, memory, workflow |
| **7 evaluators** | score a component; evaluators run under a **read-only tool guard** so scoring cannot mutate |
| **7 optimizers** | rewrite an existing component's source and re-register it live |
| **`enable_evolving` gate** | frozen components (the evolution agents themselves) can never be optimized — checked first, every time |
| **ExtensionManager** | writes the flat active file, archives every version under `extension/.versions/`, records the active one in `manifest.json` |
| **Journal + smoke gate** | promotion is journaled and guarded by replay-based smoke checks before it goes live |
| **Rollback** | `rollback(module, name, version)` restores any archived version |

Generated components live **outside** the Python package, in the external
[`extension/`](extension/) tree — `agentevolver/` stays immutable. There is **no `__init__.py` to
edit**: loading is directory scan + dynamic import, and registration goes through the same registries
the built-ins use. The playbook is a skill itself: `skill/creator/self_evolving_skill`.

### HTML-native everything

This is the framework's signature idea: **the artifacts an agent reads, writes and executes are the
same files a human opens in a browser.** No parallel "export to HTML" step that can drift.

| Artifact | File | Executable by | Rendered by |
|---|---|---|---|
| **Agent prompt** | `prompt/default/<agent>.html` | `prompt_manager` | `visual/css/prompt.css` + `js/prompt.js` |
| **Dynamic workflow** | `workflow/default/<name>.html` | `WorkflowCompiler` → `WorkflowRuntime` | `visual/css/workflow.css` + `js/workflow.js` |
| **Task document** | `examples/tasks/*.html` | `task/loader.py` | `visual/css/task.css` + `js/task.js` |
| **Memory report** | `FileSystemMemory` output | — (JSON twin for the runtime) | `visual/css/memory.css` |
| **Step snapshot** | `<log_root>/messages/<agent>/NNNN.html` | — | the *same* `prompt.css` / `prompt.js` |

<details>
<summary><b>Prompts: a document, not an f-string</b></summary>

Each agent's prompt is one complete HTML file. A SAX parser pulls `<meta>` attributes for identity
and versioning, then extracts `div.system` and `div.user` verbatim — inner markup is preserved, so
the semantic tags survive into the model's context:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta name="name" content="meta_agent">
  <meta name="version" content="2.9.0">
  <meta name="enable_evolving" content="false">
  <link rel="stylesheet" href="../../visual/css/prompt.css">
  <script src="../../visual/js/prompt.js"></script>
</head>
<body>
<div class="system">
  <profile>You are a Meta Agent — an orchestrator that …</profile>

  <module src="../module/language_settings.html"></module>

  <project>
    - `{{ workspace_root }}` — the Session's user workspace …
  </project>

  <module src="../module/runtime.html"></module>
  <constraint-rules>…</constraint-rules>
  <task-rules>…</task-rules>
</div>
</body>
</html>
```

- **`<module src="...">`** pulls in reusable fragments from [`prompt/module/`](agentevolver/prompt/module/) —
  `action_batching`, `agent_context`, `constraint_rules`, `context_rules`, `language_settings`,
  `progress_rules`, `response_protocol`, `runtime`. One edit propagates to every agent that includes it.
- **`{{ jinja2 }}` variables** are rendered at call time with the run's real roots and live context.
- **Versioning is in the file** (`<meta name="version">`), so an optimizer agent rewriting a prompt
  produces a diffable artifact with an explicit version bump — not an opaque string change.
- Because the file links the stylesheet, **double-clicking it opens a styled, readable document**.
  The runtime never executes that CSS/JS: they exist purely so humans can read what the model reads.

</details>

<details>
<summary><b>Snapshots: see the exact prompt of every step</b></summary>

`snapshot_hook` writes each think-and-act step's *rendered* messages — the prompt with every field
filled in — to `<log_root>/messages/<agent_name>/0001.html`, `0002.html`, … wrapped in the same
`div.system` / `div.user` and linked to the same stylesheet.

The result: for any step of any run you can open a browser and see **exactly** what the model saw,
styled identically to the prompt template it came from. Debugging an agent stops being a log-grep.

</details>

<details>
<summary><b>Workflows: reviewable HTML that is also a multi-agent program</b></summary>

A workflow is orchestration infrastructure — not an Agent subtype and not a fixed DAG. The `<workflow>`
element inside an ordinary HTML document *is* the source; `WorkflowCompiler` reads it directly, while
`visual/js/workflow.js` renders a preview beside it without touching the source tree.

```html
<workflow name="parallel_review" schema-version="1.1.0" version="1.0.2"
          max-agents="40" max-concurrency="8" enable-evolving="false">
  <inputs>
    <input name="task" type="string" required="true" />
    <input name="perspectives" type="array" required="true" />
    <schema for="perspectives">{"type":"array","items":{"type":"string"},"minItems":1}</schema>
  </inputs>
  <flow>
    <map id="reviews" items="${inputs.perspectives}" as="perspective" concurrency="8">
      <agent id="review" name="general_agent" task="Review from the ${perspective} perspective: ${inputs.task}" />
    </map>
    <verify id="verified" items="${reviews}" as="finding" agent="general_agent"
            task="Independently verify this review and reject unsupported claims: ${finding}" />
    <reduce id="report" items="${verified}" agent="general_agent"
            task="Deduplicate verified findings, rank by impact, produce one concise report." />
  </flow>
  <outputs><output name="report" value="${report}" /></outputs>
</workflow>
```

**Language**: `<agent>` `<tool>` `<skill>` `<connector>` `<environment>` `<workflow>` invoke registered
capabilities; `<parallel>` `<map>` `<reduce>` `<branch>` `<loop>` `<verify>` `<checkpoint>` are the
control flow. Expressions are a restricted `${path.to.value}` syntax.

**Safety invariants** — no JavaScript, Python, event handlers, filesystem operations or shell commands
execute from workflow HTML; only compiler-whitelisted tags become instructions; loops require
`max-rounds`; fan-out, nesting, agent count, concurrency and wall time are all bounded; side effects
go through the normal capability managers and keep their permission boundaries.

**Execution model** — `WorkflowDefinition` → `ExecutionFrame` → `InvocationRun` → `InvocationAttempt`,
each with an explicit state machine and transition table. Runs can be paused, resumed and cancelled;
checkpoints are written atomically with an executable program hash, so a resume caches completed
invocations, restarts incomplete ones, and *rejects* a same-version program that has changed.

Active workflows are projected to the MetaAgent as native functions named `workflow__<name>`.
→ [`docs/workflows.md`](docs/workflows.md)

</details>

### The capability ecosystem

Nine kinds of capability, one uniform contract. Every callable manager implements
`get_schema(name, action=None, format="json"|"md")` — `json` returns the exact native function-calling
object sent to the model, `md` returns a human-readable contract. The prompt carries only a **compact
roster**; full schemas travel in the model request, and `inspect_*` tools fetch details on demand.

| Kind | What it is | Built-in count |
|---|---|---|
| **Agent** | reasoning + execution loop; actors, generators, evaluators, optimizers | 29 |
| **Tool** | one atomic operation — bash, file r/w/edit, git, grep/glob, web fetch/search, code interpreter, todo, deploy, escalate/reply, `inspect_*` | 27 |
| **Skill** | filesystem-backed SOP (`SKILL.md` + scripts/references) in 11 categories | 83 |
| **Connector** | an external **MCP server**, each action projected as its own function (`<connector>__<action>`) | 25 |
| **Environment** | stateful world with `@action` methods — browser, Linux desktop, SSH, artifact renderer | 4 |
| **Workflow** | executable HTML multi-agent program | — |
| **Memory system** | pluggable per-session memory, tiered and itself evolvable | 2 |
| **Plugin** | one outside service (OpenAI, Tavily, Chroma, Composio, …) providing several canvas datasource tools | 89 / 332 tools |
| **Knowledge · Process** | RAG over named corpora (`bm25`, `tfidf`, …); pure record/text transforms | — |

<details>
<summary><b>Skill categories</b></summary>

`creator/` self-evolution playbooks · `methodology/` TDD, incremental dev, debugging, API design, git ·
`orchestrate/` task breakdown, spec/doubt-driven dev, context engineering · `review/` code & security
review, verify, simplify, performance · `authoring/` docx/pdf/pptx/xlsx, report & artifact design ·
`web/` frontend, webapp testing, deploy, CI/CD, migration · `research/` deep research, observability ·
`science/` AlphaFold2, Boltz, Chai-1, DiffDock, ESM2/ESMFold, Evo2, LigandMPNN/ProteinMPNN, scGPT,
scvi-tools, literature review · `writing/` a full research-paper pipeline · `interactive/` · `misc/`

</details>

<details>
<summary><b>Built-in MCP connectors (bio/chem research)</b></summary>

`pubmed` · `biorxiv` · `chembl` · `chemistry` · `ketcher_chemistry` · `molecule_toolkit` · `zinc` ·
`biomart` · `cbioportal` · `cellguide` · `clinical_genomics` · `clinical_trials` · `drug_regulatory` ·
`expression` · `genes_ontologies` · `genomes` · `human_genetics` · `literature_graph` ·
`omics_archives` · `protein_annotation` · `regulation` · `research_resources` · `rna` ·
`structures_interactions` · `variants`

</details>

### Agent runtime & protocol

`runtime/` is *how messages move*; `protocol/` is *the shape of each conversation*.

- **One lifecycle for every agent** — tool-calling agents share
  `on_start → _advance → _think → _dispatch_round → _run_one → _conclude`; deterministic
  `ProceduralAgent` implementations reuse the same entry and hooks but implement `run_procedure`.
- **Runtime verbs** (`runtime_manager`): `spawn`, `send` (fire-and-forget), `ask` / `invoke`
  (run + await a `Response`), `suspend` + `resume` (park on a key), `publish` + `subscribe` (fan-out).
- **Protocol channels** (`protocol_manager`) are typed conversations over those verbs: **escalation**
  (a blocked sub-agent asks its parent and suspends until it replies), **delegation** / **query**,
  **progress** / **control** (cancel, pause, resume), **pubsub**.

<details>
<summary><b>What <code>spawn</code> actually wraps an agent in</b></summary>

An `Agent` is a class with a `handle(msg, ref)` method; it has no loop of its own. `spawn` gives it
an **`AgentRef`** — a handle holding a private `asyncio.Queue` inbox, a pump task, a status
(`RUNNING` / `STOPPING` / `STOPPED` / `DEAD`) and a slot for a pending reply. Everything else in the
framework addresses the ref, never the object.

```
   send / ask ──▶ ref._inbox ──▶ ref._pump_task ──▶ agent.handle(msg, ref)
                  (asyncio.Queue)   drains forever      the agent's own work
```

The pump **owns nothing**: it drains the inbox and dispatches, and that is all. Two exits, both
deliberate — a `StopMessage` makes it return cleanly (so a graceful stop drains what is already
queued first), and an unhandled exception marks the ref `DEAD` rather than leaving a half-live agent
that silently accepts messages nobody will ever process. Sending to a ref that is not `RUNNING`
raises `AgentDeadError` instead of dropping the message on the floor.

Because the loop belongs to the ref rather than to the agent, one agent is one mailbox: work arrives
from a parent agent, the Gateway, a tool or another session through the same door, in arrival order.

</details>

<details>
<summary><b>Interrupt and resume: parking a coroutine on a key</b></summary>

`suspend(key, timeout=…)` registers a **one-shot future** under `key` and blocks the caller;
`resume(key, value)` resolves it and returns *whether anyone was actually waiting* — so a reply that
arrives after a timeout is a `False`, not a crash. A second waiter on a live key is a
`Suspend key collision`, never a silent overwrite.

**Escalation** is the primary user. A sub-agent that cannot proceed posts an `EscalationMessage`
(reason, situation, suggestion) to its parent and then suspends **on its own `task_id`**:

```
  sub-agent          parent
     │ escalate ───────▶ │  (EscalationMessage lands in the parent's inbox)
     │ suspend(task_id)  │
     ⏸  parked           │  the parent keeps working, decides, then:
     │ ◀─── reply(task_id, guidance)      → runtime.resume(task_id, guidance)
     ▶ carries on with the guidance
```

While parked the coroutine holds no loop and burns no budget. **Nothing hangs forever**: no parent,
a parent that has died, or a timeout each return a graceful-stop instruction, so the subtask ends on
its own terms instead of blocking until the run is killed.

One subtlety worth knowing about, because getting it wrong is subtle and fatal: a caller's timeout on
`ask` must **not** cancel the future the agent owns. The in-flight handler may still complete
normally, and cancelling the shared future would turn that completion into an `InvalidStateError`
that takes the long-lived pump down with it. The wait is therefore shielded — a slow answer is late,
not lethal.

Alongside that, `ControlMessage` carries `cancel` / `pause` / `resume` to an agent that is *already
mid-flight*, and `QueryMessage` asks a running agent for a status snapshot without disturbing it.

</details>

### Hooks: cross-cutting behaviour, one pipeline

| Hook | What it does |
|---|---|
| `trace_hook` | emits a structured `TraceEvent` for every lifecycle event |
| `trajectory_hook` | builds step-level training trajectories as the run happens |
| `memory_hook` | feeds lifecycle events into the memory systems |
| `constraint_hook` | enforces per-step resource budgets |
| `no_progress_hook` | blocks an unchanged, already-successful action batch **before** it executes |
| `snapshot_hook` | saves each step's rendered messages as HTML |
| `compact` | summarises overflowing record lists into working memory |
| 6 × `*_registration_hook` | register a generated tool / skill / agent / environment / connector / workflow live |

The **no-progress guard** deserves a note: it's stateless (evidence lives on each agent run, so
concurrent sessions can't affect one another) and **universal** — wired into the base
`Agent._prepare_round`, the single round path every agent's loop flows through, so no agent can opt
out. `MetaAgent` overrides that method only to add orchestration and still chains to `super()`.
Tools can declare a `progress_policy` (`workspace` / `external` / `polling` / `always`) to tell the
guard when repetition is legitimate.

### Memory: a state machine, not a log

Per-session, pluggable (`MEMORY_SYSTEM` registry), itself evolvable. The default `TieredMemory`
(JSON via `GeneralMemorySystem`, HTML via `FileSystemMemory`) is driven by `TraceEvent`s:

- **`emit(event)`** syncs into four views: **todos**, **flow_chart** (the call path),
  **recent_history** (raw log), **final_result**.
- **Two tiers** — `recent_history` is raw and bounded; on overflow the oldest records are summarised
  by the `compact` hook into `working_memory`. `get()` injects the last N summaries plus the last N
  raw records into the prompt.

### Constraints: budgets the agent can read

Enforcement alone isn't enough — an agent that gets killed at step 50 loses everything it hadn't
written down. So `constraint/` does two things:

1. **Enforce.** `step_constraint`, `token_constraint`, `wall_time_constraint` (all registered via
   `CONSTRAINT`, all evolvable) return `Response(success=False, …)` when violated, and
   `constraint_hook` checks them every step.
2. **Inform.** Each check also returns a `ConstraintStatus` (`used` / `limit` / `unit`, with derived
   `remaining` and `ratio`). `render_status_text()` turns those into a prompt block injected into
   `agent_context` on **every** step:

```
You operate under hard resource limits. When any limit is hit the task is force-stopped
immediately — an unfinished answer is lost. Budget accordingly.

Current budget (used / limit, remaining):
- step_constraint: 34 / 50 (16 steps remaining)
- token_constraint: 412,880 / 600,000 (187,120 tokens remaining)
- wall_time_constraint: 1180s / 3600s (2420s remaining)

Status: TIGHT. Stop broadening scope; prioritize the critical path and prepare to conclude.
```

The tier comes from the **most-consumed** budget: **NORMAL** → **TIGHT** at 60% → **CRITICAL** at 85%,
each with a policy hint the prompt's `<constraint-rules>` module teaches the agent to act on (at
CRITICAL: consolidate verified work and call `done_tool` with the best partial result). Limits can be
overridden per call and the effective value is remembered per task, so an agent can raise or lower
its own budget mid-run and the status stays consistent.

### Observability & training data

| Module | Output |
|---|---|
| `trace/` | structured `TraceEvent`s, persisted to `<log_root>/trace/<session_id>.jsonl` and fanned out to subscribers — the Gateway forwards them live to the browser |
| `trajectory/` | the same run projected into **reward-annotated, step-level training records**, exported in SFT/RL formats (e.g. VERL) |
| `memory/` | the human-readable HTML report and the working-memory tiers |
| `benchmark/` | AIME24/25, GPQA, GSM8K, HLE, LeetCode, DeepWeb, ProgramBench, exact-match — reading `datasets/` first and falling back to a HuggingFace snapshot (`ensure_dataset`, `HF_ENDPOINT`-aware) |

Trace is strictly observational: it must never change Agent, Runtime or Workflow execution semantics.

---

## 🖥 The Web UI

One React/Vite SPA over one WebSocket. The Gateway exposes **84 commands** and a versioned,
**replayable** event stream (`GatewayCommand` / `GatewayResponse` / `GatewayEvent`, each carrying a
`protocol_version`, a monotonic `seq_no` and the owning `session_id` / `conversation_id` / `task_id`).

<div align="center">

<a href="https://dvampire.github.io/AgentEvolver/ui.html"><img src="docs/assets/ui/01-overview.jpg" width="100%" alt="The workbench: projects and views on the left, the task surface in the middle, session state on the right"></a>

### ▶ [Watch the tour](https://dvampire.github.io/AgentEvolver/ui.html) — eleven short clips, one per feature

<sub>GitHub strips <code>&lt;video&gt;</code> from READMEs, so the clips play on the docs site. Thumbnails below jump straight to one.</sub>

</div>

<table>
<tr>
<td width="33%" align="center"><a href="https://dvampire.github.io/AgentEvolver/ui.html#canvas"><img src="docs/assets/ui/03-canvas.jpg" width="100%" alt="The Canvas node editor with its component palette"></a><br><b>🕸 Canvas</b><br><sub>Wire a flow from a component palette</sub></td>
<td width="33%" align="center"><a href="https://dvampire.github.io/AgentEvolver/ui.html#code"><img src="docs/assets/ui/04-code.jpg" width="100%" alt="VS Code in the browser, rooted at the session workspace"></a><br><b>📝 Code</b><br><sub>Real VS Code on the session's files</sub></td>
<td width="33%" align="center"><a href="https://dvampire.github.io/AgentEvolver/ui.html#science"><img src="docs/assets/ui/05-science.jpg" width="100%" alt="The notebook kernel and the live compute panel"></a><br><b>🔬 Science</b><br><sub>A kernel you and the agent share</sub></td>
</tr>
<tr>
<td width="33%" align="center"><a href="https://dvampire.github.io/AgentEvolver/ui.html#capabilities"><img src="docs/assets/ui/06-capabilities.jpg" width="100%" alt="The capability catalogue with live counts"></a><br><b>🗂 Capabilities</b><br><sub>Pick what this session may use</sub></td>
<td width="33%" align="center"><a href="https://dvampire.github.io/AgentEvolver/ui.html#machines"><img src="docs/assets/ui/09-machines.jpg" width="100%" alt="Local noVNC machines, remote SSH hosts and deployments"></a><br><b>📺 Machines</b><br><sub>A desktop the agent can drive</sub></td>
<td width="33%" align="center"><a href="https://dvampire.github.io/AgentEvolver/ui.html#models"><img src="docs/assets/ui/10-models.jpg" width="100%" alt="The model catalogue grouped by provider"></a><br><b>🧠 Models</b><br><sub>Every provider in one catalogue</sub></td>
</tr>
</table>

| View | What it is |
|---|---|
| 💬 **Chat** | task composer with file upload, live activity timeline, approval dialogs, cancellation, event inspector, auto-reconnect and replay |
| 🕸 **Canvas** | a visual flow editor (React Flow). Flows are **JSON** — the editable source of truth — compiled to a `WorkflowDefinition` and run *ephemerally* on the shared `workflow_runtime`. Deliberately isolated from agent HTML workflows: they share the execution engine like two languages sharing one VM, but not identity, storage or authoring. → [`docs/canvas.md`](docs/canvas.md) |
| 📝 **Code** | **real VS Code in the browser** (openvscode-server), one container per session, editing the same workspace bytes the agent edits |
| 🔬 **Science** | a workstation: conversation + live Jupyter kernel + compute panel + notebook history + one-click JupyterLab |
| 📺 **VNC** | watch the browser or Linux-desktop environment live via noVNC while the agent drives it |

Plus: a capability browser (every agent/tool/skill/connector/environment/workflow/command/canvas
entry, tagged `default` vs `extension` and whether it self-evolves), per-session capability selection,
a model manager, a workspace file editor (Monaco), deploy site status, SSH host management, and
extension staging/promotion. An **Ink terminal client** (`npm run dev:terminal`) speaks the same
protocol.

<details>
<summary><b>Why the IDE and JupyterLab are served under a path on the UI's own origin</b></summary>

VS Code emits **absolute** asset paths, so a sub-path only works if the server knows about it —
`--server-base-path /ide/<session>` makes both sides agree, and every hop strips exactly the prefix
it added, so VS Code never learns it is proxied. JupyterLab does the same with
`--ServerApp.base_url=/science/<session>/`.

```
<ui origin>/                   → AgentEvolver SPA
<ui origin>/ide/<session>/     → that session's VS Code
<ui origin>/science/<session>/ → that session's JupyterLab
```

This used to be a per-session *hostname* (`<session>.ide.localhost`), which is resolved by the
**browser** — so it only ever worked with the browser running on the machine serving the UI. Through
a tunnel the iframe pointed at the user's own laptop and came up blank.

**IDE state**: `/workspace` is per **session** (the agent's files, same bytes, no copy); extensions,
user data and `$HOME` are per **owner**, so a new session is isolated but never makes you reinstall
your plugins — and an agent CLI sign-in (`~/.claude`, `~/.codex`) outlives the container. The IDE
container **never mounts the Docker socket**. Lazy start on first open, idle reaper, LRU eviction at
`max_instances`.

</details>

<details>
<summary><b>One kernel, so there is nothing to synchronise</b></summary>

The agent's `code_interpreter_tool`, the Science REPL and JupyterLab all execute through the **same**
Jupyter Server, one per project. So a variable the agent defined is a variable you can print, and the
"Notebook" panel isn't a document anyone edits — it's **the kernel's own execution history**,
rendered. Nothing can drift, because there is only one record and one set of variables.

Cells return full MIME bundles, so a matplotlib figure arrives as an actual `image/png` instead of
the string `<Figure size 640x480 with 1 Axes>`. `science.save` copies the history out as a real
`.ipynb` when you want a file. The reaper checks idle **and** the server's own `execution_state`, so
a six-hour training run with nobody watching is never mistaken for an abandoned session.

**Three levels, deliberately**: a *project* (`session_id`) owns workspace files, kernels and
containers; a *conversation* (`conversation_id`) owns the transcript, agent memory, budgets and
todos; a *task* (`task_id`) owns one submission's trajectory. Resources key off the project, state
keys off the conversation — so a fresh question doesn't arrive carrying the last one's context, and
asking one doesn't cost a container.

</details>

---

## 🔒 Sandbox & safety

Four independent layers, each doing one job.

### 1. Isolation — the whole framework runs in a container

`SANDBOX` backends: `host`, `docker`, `playwright`, `chrome_vnc`, `computer` (desktop), `vscode`
(E2B and a Docker-native backend are scaffolded adapters to the same contract). `SandboxConfig`
declares image, entrypoint, env, mounts, published ports, user, workdir, lifetime and the network
policy below.

### 2. Egress — there is no way out, not a filter that must catch everything

```
   ┌─ container ─────────────────────────────┐          ┌─ host ──────────────────────┐
   │  agent process                          │          │                             │
   │     │ HTTPS_PROXY=127.0.0.1:<port>      │          │   relay.py                  │
   │     ▼                                   │          │     ├─ NetworkPolicy check  │
   │  forwarder.py ──── unix socket ─────────┼──────────┼──▶  ├─ record the attempt   │
   │                                         │          │     └─ open, or refuse      │
   │  (no network interface — loopback only) │          │                             │
   └─────────────────────────────────────────┘          └─────────────────────────────┘
```

A task sandbox usually needs *some* network — the agent brain has to reach a model endpoint — while
the work itself must not reach the open internet. A boolean flag can't express that, so:

- The container is given **no network interface at all**, only loopback. Denial is the default state
  of the world, not a rule someone must remember to apply.
- Its single route out is a Unix socket bind-mounted from the host. **A Unix socket, not a loopback
  port**: file permissions are a boundary; a listening port would be reachable by anything else on
  the machine.
- Policy is evaluated **outside** the sandbox, in a process the sandboxed code cannot reach — a
  policy checked inside is a policy the sandboxed process can edit.
- **Matching rules**: `deny` beats `allow`, always. Entries are a bare host, a wildcard
  (`*.example.com`, sub-domains but not the bare domain), or `host:port`. Case-insensitive.
  With `default_allow=False` (the norm) anything unmatched is **denied**, so a forgotten host fails
  closed.
- The allowlist for model calls is derived from the deployment's own `*_API_BASE` variables, not
  hardcoded — an allowlist naming a provider's public host would look correct and block every call
  behind a relay.
- **Every attempt is recorded**, so "the work had no internet access" becomes a claim a run can
  substantiate rather than a hope about a config file.

### 3. Authorization — permission modes and command intent

`permission_manager` evaluates a proposed operation before Tool or Sandbox executes it.

| | |
|---|---|
| **Modes** | `read_only` · `workspace_write` · `danger_full_access` |
| **Operations** | `bash` · `read` · `write` |
| **Command intent** | shell commands are classified as `read_only`, `write`, `destructive`, `network`, `process_management`, `package_management`, `system_admin` or `unknown` |
| **Safeguards** | file-size and binary-file guards on reads and writes |

### 4. Housekeeping — crash-safe, de-conflicted

- **Sandbox ledger** — a write-ahead log records a container id right after creation and forgets it
  on clean destroy. Whatever remains at the next boot belongs to a dead run and is force-removed
  before the first new sandbox starts. (Leaked peers used to make every subsequent browser start
  fail with a "network connectivity error" that silently emptied the capability list.)
- **Port registry** — named defaults (`GATEWAY` 9876, `OPENSANDBOX` 8080) plus one
  `register(name, port=None, preferred=…, kind=…)` interface, persisted to
  `output/.runtime/ports.json` so every process and every run sees the same map. An environment owns
  the *value* of its ports (a browser sandbox knows its own CDP/VNC ports) but still registers them
  centrally.
- **Deploy** — `DEPLOYER` profiles (`static`, `node`, `python`, `llm`, `custom`) run a web artifact
  inside a sandbox and bind a URL, recording health, resources and lifecycle state.

---

## 📁 Output & project layout

`agentevolver` has one CLI with three modes: a control command (`agentevolver /registry`), the
terminal loop (`agentevolver tui`), or the Gateway (`agentevolver serve ...`).

There are two writable roots, and only two. Every path the framework writes is declared in one table
(`agentevolver/paths`) and resolved through `path_manager` — nothing else joins path fragments, so
**this module is the disk contract**. `P` is a `str` enum, so a typo is a static error with editor
completion, and `get()` validates placeholders (asking for `SESSION_WORKSPACE` with only `owner`
raises instead of creating a directory literally named `{session_id}`).

```
output/                          generated state — disposable
  .runtime/                      machine-level, belongs to the host
    ports.json                     port registry
    sandbox_ledger.json            crash-safe container reaping
    deploy/  checkpoints/  staging/
  <owner>/
    state/                       durable across sessions: files, flows, IDE extensions + logins
      files/  flows/  ide/{extensions,user-data,home}
    sessions/<session-id>/       one task, one directory
      workspace/                   the files agent, canvas and IDE all share
      session.json                 identity, so a session survives a restart
    runs/<run-id>/               direct (non-gateway) runs
extension/                       shared, durable components — versioned with the project
  tool/ agent/ prompt/ skill/ connector/ environment/ memory/ canvas/
  .versions/                     every archived version
  manifest.json                  which version is active
```

A task started from a config file and the same task started from the browser resolve to the *same*
directory: both build their sandbox from the layout rather than joining paths themselves. A session
stages extension changes under its own output directory and promotes them into `extension/`
explicitly.

`AGENTEVOLVER_HOME` moves the whole tree elsewhere (a shared volume, a scratch disk);
`AGENTEVOLVER_EXTENSION_ROOT` moves just the shared component library. `writable_roots()` returns
exactly these two, so the rule is testable rather than a convention people remember — see
[`tests/test_paths.py`](tests/test_paths.py).

<details>
<summary><b>Repository map</b></summary>

```
AgentEvolver/
├── agentevolver/            # the framework — 48 modules, each with its own README.md
│   ├── agent/               #   actor/ generator/ evaluator/ optimizer/
│   ├── tool/ skill/ connector/ environment/ workflow/ memory/ plugins/
│   ├── prompt/              #   HTML prompts + reusable module/ fragments
│   ├── visual/              #   dependency-free browser renderers for those HTML artifacts
│   ├── runtime/ protocol/ hook/ trace/ trajectory/
│   ├── sandbox/ permission/ constraint/ port/ deploy/ docker/ e2b/
│   ├── gateway/ frontend-facing: canvas/ ide/ science/ kernel/ conversation/
│   ├── knowledge/ process/ data/ benchmark/
│   ├── paths/ session/ task/ config/ version/ dynamic/ extension/ capability/
│   └── registry.py          #   the mmengine Registry instances
├── configs/                 # mmengine configs (base.py, meta_agent.py, agents/, tools/, memory/)
├── docker/                  # base · vscode · computer (XFCE desktop) · chrome-vnc images
├── frontend/                # React/Vite web UI + Ink terminal client
├── extension/               # hot-pluggable evolved components (outside the package)
├── examples/                # run_*.py per agent + task documents under tasks/
├── datasets/ · docs/ · scripts/ · tests/
```

</details>

---

## 🧩 Extending the framework

Components self-register with an [mmengine](https://github.com/open-mmlab/mmengine) `Registry` via a
class decorator. Built-ins register at import time; extensions are registered at runtime by the
ExtensionManager through the same registries.

| Registry | Location | Decorator |
| --- | --- | --- |
| `TOOL` | `agentevolver.tool` | `@TOOL.register_module()` |
| `AGENT` | `agentevolver.agent` | `@AGENT.register_module()` |
| `PROMPT` | `agentevolver.prompt` | `@PROMPT.register_module()` |
| `SKILL` | `agentevolver.skill` | `@SKILL.register_module()` |
| `HOOK` | `agentevolver.hook` | `@HOOK.register_module()` |
| `CONSTRAINT` | `agentevolver.constraint` | `@CONSTRAINT.register_module()` |
| `ENVIRONMENT` | `agentevolver.environment` | `@ENVIRONMENT.register_module()` |
| `MEMORY_SYSTEM` | `agentevolver.memory` | `@MEMORY_SYSTEM.register_module()` |
| `SANDBOX` | `agentevolver.sandbox` | `@SANDBOX.register_module()` |
| `DEPLOYER` | `agentevolver.deploy` | `@DEPLOYER.register_module()` |
| `PLUGIN` | `agentevolver.plugins` | `@PLUGIN.register_module()` |
| `DATASET` · `BENCHMARK` | `agentevolver.data` · `.benchmark` | `@DATASET…` · `@BENCHMARK…` |
| `E2B` · `DOCKER` | `agentevolver.e2b` · `.docker` | `@E2B…` · `@DOCKER…` |

> **Not registry-based:** `connector` (MCP servers), `protocol` and `trajectory` are owned by their
> `*_manager` singletons directly — connectors are discovered by scanning `CONNECTOR.md` directories,
> the way skills scan `SKILL.md`.

**Conventions**

1. **Hand-written built-ins** go in the module's `agentevolver/<module>/default/` folder and must be
   imported in that folder's `__init__.py` (import + `__all__`) so the decorator runs. Skills,
   connectors and plugins use category/package sub-folders instead.
2. **Generated or evolved components** go in the external `extension/` tree — never in
   `agentevolver/`. Write the flat active file (`extension/tool/<name>.py`,
   `extension/agent/<name>.py` + `extension/prompt/<name>.html`,
   `extension/skill/<name>/SKILL.md`, `extension/connector/<name>/CONNECTOR.md`,
   `extension/environment/<name>.py`); the ExtensionManager registers and archives it.
   **Do not edit any `__init__.py`** for extensions — discovery is by directory scan.
3. **Keep the module contract**: subclass the base class in `types.py`, implement its abstract
   methods, and go through the module's `*_manager` singleton in `server.py`.
4. **Benchmarks read `datasets/` first**, then snapshot-download from HuggingFace via
   `ensure_dataset(<name>, hf_repo_id)`.

Each module's own `README.md` is the authoritative contract for its boundary, and
[`tests/test_module_readmes.py`](tests/test_module_readmes.py) keeps them honest.

---

## 📚 Documentation

| Document | Contents |
|---|---|
| [`PROJECT.md`](PROJECT.md) | the full directory structure, key concepts and conventions |
| [`docs/workflows.md`](docs/workflows.md) | authoring dynamic HTML workflows |
| [`docs/canvas.md`](docs/canvas.md) | the visual flow editor |
| [`docs/capability-schemas.md`](docs/capability-schemas.md) | the shared capability schema protocol |
| [`scripts/INSTALL.md`](scripts/INSTALL.md) | manual setup, Vault, optional extras |
| [`frontend/README.md`](frontend/README.md) | the web UI in detail |
| [the UI tour](https://dvampire.github.io/AgentEvolver/ui.html) | a clip per feature — regenerate with [`scripts/record-ui-clips.py`](scripts/record-ui-clips.py) and [`scripts/encode-ui-clips.sh`](scripts/encode-ui-clips.sh) |
| `agentevolver/<module>/README.md` | 48 per-module contracts |

## 📄 License

[MIT](LICENSE) © 2026 Wentao Zhang
