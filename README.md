<div align="center">

# AgentEvolver

### Multi-agent collaboration and global evolution for complex work

AgentEvolver is a **self-evolving multi-agent platform** for engineering and research.
A shared Runtime coordinates agents and capability calls; a living Plan carries the goal,
progress and next steps through long tasks. Agents can improve **eight kinds of reusable
entities**—Tools, Skills, Agents, Connectors, Workflows, Memory, Environments and Plugins—
with versioned candidates, evaluation evidence and subsequent use recorded along the way.

Overview, Chat, Canvas, Code and Science bring that work into one project workspace.
Execution trajectories also provide a data foundation for downstream SFT/RL training.

[![License](https://img.shields.io/badge/license-MIT-5B6CFF.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![Quality](https://github.com/DVampire/AgentEvolver/actions/workflows/quality.yml/badge.svg)](.github/workflows/quality.yml)
[![Docs](https://img.shields.io/badge/docs-online-20B2AA.svg)](https://dvampire.github.io/AgentEvolver/)

**[Website](https://dvampire.github.io/AgentEvolver/)** ·
**[Global evolution](#global-evolution-across-eight-entities)** ·
**[Runtime & Plan](#runtime-and-plan)** ·
**[Quick start](#quick-start)** ·
**[How it works](#how-it-works)** ·
**[Web workbench](#web-workbench)**

中文：**[README_zh.md](README_zh.md)**

<img src="docs/assets/arch.svg" alt="AgentEvolver architecture: shared Runtime, living Plan, layered context and reusable capability evolution" width="100%">

</div>

---

## Understand it in one minute

AgentEvolver connects **task execution, reusable capability improvement and human inspection**.
The MetaAgent can work directly with tools and delegate independent assignments to specialists.
During planning, feedback and verification, agents examine whether a useful method can be
improved and reused in an upcoming operation.

| Part of the system | What it contributes |
| --- | --- |
| **Global evolution** | Improve and compose eight entity types, retaining versions and evidence for adoption decisions |
| **Shared Runtime** | Coordinate agent lifecycles, messages, background work, resource access and shared budgets |
| **Living Plan** | Keep the objective, progress, blockers and next action available throughout a long task |
| **Shared workbench** | Inspect the work, edit project files and continue an analysis alongside agents |

A task can deliver code, a report or an experiment while also producing a reusable method.
Which capabilities it can use or evolve depends on the selected configuration and permissions.

## Global evolution across eight entities

**Global evolution covers the cooperating parts of the agent system.** A task may reveal a
better reasoning method, a missing operation, an inefficient observation interface or a memory
limitation. The agent selects the component type that fits the evidence and its intended consumer,
then checks both that component and the work that depends on it.

| Evolvable entity | What can improve |
| --- | --- |
| **[Tool](agentevolver/tool/README.md)** | A bounded executable operation with a clear input/output contract |
| **[Skill](agentevolver/skill/README.md)** | A reusable procedure, research method, design approach or verification checklist |
| **[Agent](agentevolver/agent/README.md)** | Specialist behavior, reasoning, planning, decomposition and its associated prompt |
| **[Connector](agentevolver/connector/README.md)** | Access to an external service or data source |
| **[Workflow](agentevolver/workflow/README.md)** | Reusable sequencing, branching and coordination of capabilities |
| **[Memory](agentevolver/memory/README.md)** | How knowledge and experience are retained, retrieved and reused |
| **[Environment](agentevolver/environment/README.md)** | Observation, interaction, state and lifecycle handling in an execution environment |
| **[Plugin](agentevolver/plugins/README.md)** | A cohesive service integration exposing related tools and shared resources |

For example, a Connector can supply data to an Agent, a Skill can guide its analysis, and a
Workflow can coordinate repeated checks. Each useful change is evaluated against its own baseline;
its dependent operations are then replayed to check the combined result. Later evidence can lead
to another improvement of the retained version.

Evolution candidates live in external `extension/` or session staging. Versions, comparison cases
and actual call references support keep, rollback and unload decisions. Promotion makes a staged
component available through shared extensions; later runs select capabilities through their own
configuration. An Agent's prompt belongs with that Agent and is not a ninth entity type.

Structural admission checks loading and schemas. Functional evaluation records a judgment about
results, and subsequent use shows how a retained version behaves in real work. A candidate can be
provisionally active before its functional evaluation; registration alone does not establish improvement.
The [extension contract](agentevolver/extension/README.md) defines these boundaries.

## Runtime and Plan

### A shared execution Runtime

The Runtime treats an agent as a managed logical process with its own state, mailbox and lifecycle.
The same kernel also manages lightweight calls across component families, while each component
owns its domain behavior. This supports collaboration patterns that can change with the task.

| Mechanism | Why it matters |
| --- | --- |
| **Dispatch and messaging** | Delegate bounded assignments, send follow-up work, ask for help and collect results |
| **Subscriptions and worker pools** | Publish an event to interested agents, or assign work to one available subscriber |
| **Background jobs and persistent terminals** | Continue useful work while a command or child runs; retain shell state across calls |
| **Shared budgets and inherited permissions** | Account for parent and child usage together and keep child actions within their grants |
| **Resource coordination and cleanup** | Coordinate declared shared/exclusive access, stop child work and retain cleanup failures |
| **Recovery with evidence** | Restore saved state and reconcile uncertain external effects before replaying work |

See the [Runtime contract](agentevolver/runtime/README.md) for lifecycle and concurrency details.

### A living Plan throughout the task

The coordinator maintains a compact `plan/index.md` with the objective, progress, blockers,
next action and links to detailed records. `plan/plan.md` holds the full plan. Runtime reads
the current index into the live context, so it remains available after conversation compaction.
Workers receive bounded assignments; the coordinator owns the shared plan and revises it as
feedback and evidence arrive.

Automatic planning and human review are separate options:

- **`auto`**: maintain and revise the plan while executing; this is the MetaAgent launcher's default.
- **`plan`**: restrict actions to permitted reading/reasoning until a person approves the plan.
  The run must include `exit_plan_mode` in its available tools.
- **`off`**: omit the planning context and automatic planning obligation.

When evolution is enabled, the plan also tracks opportunities, the next consumer, comparisons and
the distinct states of proposal, evaluation, adoption and use. See the [Plan contract](agentevolver/plan/README.md).

## Supporting capabilities

| Capability | What it adds |
| --- | --- |
| **Code Mode** | Express batches, loops and branches in a program whose capability calls pass through guarded dispatch |
| **Layered context** | Separate stable instructions, compacted history, recent interactions and live state; keep detailed records available on demand |
| **Trace and trajectories** | Inspect actual requests and action results, and export reward-annotated records for SFT/RL |
| **Versioned extensions** | Archive reusable components and support evaluation, controlled promotion and rollback |
| **Budgets and observability** | Expose remaining steps, tokens and time; retain execution records and optionally export OpenTelemetry spans |
| **Shared project tools** | Work with files, a browser IDE, a Python kernel, environments and deployment previews through one Gateway |

The [tool catalog](docs/tool-catalog.md) lists registered tools, their parameters and permission modes.

## Fit and trade-offs

### A good fit for

- long-running engineering, data, or scientific tasks that benefit from specialist agents;
- teams that want methods discovered during a task to become reusable tools, skills, or workflows;
- research into agent runtimes, self-improvement strategies, SFT/RL data construction, evaluation loops, and human oversight;
- deployments that need a visual workbench, detailed run records, rollback, and a broad extension surface.

### Probably not the right fit for

- **Simple Q&A or one-step automation:** a single agent or script will usually be lighter and faster;
- **Strict low-latency or low-token workloads:** orchestration and comparative evaluation add model calls and wall time;
- **A zero-operations hosted product:** this is a framework you deploy and extend, not a turnkey SaaS;
- **Environments that cannot tolerate experimental API changes:** the current package version is `0.1.0`;
- **Tasks with no credible acceptance criteria:** versioning and rollback control change, but cannot replace tests, benchmarks, or human review.

### Benefits and costs

| What you gain | What it costs |
| --- | --- |
| Capabilities can accumulate instead of every task starting from zero | Component contracts, evaluation rules, and extension versions need maintenance |
| Execution, evolution, evaluation, and rollback form one loop | More architecture and model usage than a single-agent system |
| A rich UI, sandboxes, connectors, and research-oriented capabilities | A larger installation; the full experience needs Docker, Node.js, and relevant credentials |
| Inspectable artifacts and exportable traces | More logs and artifacts to store and govern |
| Clear separation between core and evolved content | Rollback reduces risk; it does not make unverified extensions production-safe |

## Quick start

### Requirements

- Python 3.11+ (3.12 recommended);
- conda, or the installer's `--uv` mode;
- an API key for at least one supported model provider;
- Docker for the full Model X sandbox and container-backed environments;
- Node.js only for the Web UI; the installer handles it by default.

### 1. Install

```bash
git clone https://github.com/DVampire/AgentEvolver.git
cd AgentEvolver
bash scripts/install.sh
conda activate agentos
```

Without conda, run `bash scripts/install.sh --uv`. Heavy dependencies for browser automation,
chemistry, sandboxes, and benchmarks are opt-in:

```bash
bash scripts/install.sh --extras browser
bash scripts/install.sh --extras sandbox
bash scripts/install.sh --extras science
bash scripts/install.sh --extras all
```

See [`scripts/INSTALL.md`](scripts/INSTALL.md) for every option.

### 2. Configure a model

The [default configuration](configs/meta_agent.py) selects an `llm_hub` model route.
Set the address and key for your model gateway in `.env` at the repository root:

```bash
LLM_HUB_API_BASE='https://your-model-gateway.example/v1'
LLM_HUB_API_KEY='...'
```

Other providers use their own variables, such as `GOOGLE_API_BASE` / `GOOGLE_API_KEY`,
`ANTHROPIC_API_BASE` / `ANTHROPIC_API_KEY`, or `OPENROUTER_API_BASE` / `OPENROUTER_API_KEY`.
Select a matching registered model with `--cfg-options model_name=...`; setting a provider key
alone does not change the selected route. Provider configuration is described in the
[model guide](agentevolver/model/README.md).

Teams may manage secrets in Vault. The framework falls back to `.env` when Vault is not configured or
reachable.

### 3. Run the first task

Start with the shortest host-based path:

```bash
python examples/run_meta_agent.py \
  --task "Write a Python function that reverses a string and add unit tests."
```

You can also run an HTML or Markdown task document:

```bash
python examples/run_meta_agent.py \
  --task-file examples/tasks/qsar_egfr_experiment.html
```

Common options:

| Option | Purpose |
| --- | --- |
| `--task "<text>"` | Submit inline task text; takes precedence over `--task-file` |
| `--task-file <path>` | Run a `.html` or `.md` task document |
| `--config <path>` | Select a configuration; defaults to `configs/meta_agent.py` |
| `--cfg-options key=value ...` | Override the model, budgets, or other config values for this run |
| `--plan-mode auto / plan / off` | Choose automatic planning, an approval gate, or no planning context |

The default configuration keeps a small resident set: MetaAgent, `code_agent` and basic execution
capabilities. Select the skills, environments and evolution capabilities required by your task.

Every run gets an isolated session. Work files, logs, task views, and memory reports are written under
`output/<owner>/sessions/<session-id>/`.

### 4. Use the full containerized mode

AgentEvolver calls the “entire framework in one base container” setup **Model X**. The MetaAgent,
sub-agents, and tools share a reproducible environment; browser and desktop services start as peer
containers when required.

```bash
docker build -f docker/base/Dockerfile -t agentevolver/base:latest .

scripts/run-in-sandbox.sh -- python examples/run_meta_agent.py \
  --task "Analyze this repository and propose improvements backed by tests."
```

Add `--gpus` for NVIDIA GPU access. The launcher requires a reachable Docker daemon and never silently
falls back to host execution.

### 5. Start the Web workbench

```bash
scripts/run-in-sandbox.sh -- scripts/serve-ui.sh
```

Open `http://127.0.0.1:5173`. The default Gateway is `ws://127.0.0.1:9876/ws`.
When binding outside loopback, set `AGENTEVOLVER_GATEWAY_TOKEN` and restrict allowed browser origins.

This command uses the base image built in step 4, including its Jupyter dependencies.
The first Code launch builds the editor image and installs its default extensions, which can take
several minutes. See the [IDE image guide](docker/vscode/README.md).

For a host-based Gateway, install the optional backends in its Python environment:

```bash
python -m pip install -e '.[sandbox,science]'
```

Then follow the [local frontend instructions](frontend/README.md). Code still needs Docker and
the local base image; Science uses the host Gateway's Python environment.

### 6. Verify the installation

```bash
pytest -q
pytest -m integration   # requires external credentials, services, or peer containers
```

The default suite excludes integration tests and therefore needs no external API keys. The installer
also runs a quick verification pass.

## How it works

### Task execution and capability improvement

```text
User / Web UI
      │
      ▼
Gateway / Task Manager
      │
      ▼
Coordinator ── MetaAgent / Website Builder / Game Builder
      │
      ├── Living Plan: goal, progress, feedback and next action
      │
      ├── Shared Runtime: direct work, delegation, messages and jobs
      │       └── Scoped tools, skills, agents and other capabilities
      │
      └── Evidence-backed evolution opportunity
              └── Shared self-evolution loop across eight entity types
                      └── Candidate version → evaluate → decide → use again
```

MetaAgent and the domain Builders share the agent lifecycle, Runtime and evolution policy.
When the required capabilities are available, the working agent follows `self_evolving_skill`
within its action loop to generate, optimize and evaluate reusable components.

### The self-evolution loop

1. **Observe and choose.** Identify a reusable limitation or opportunity from planning,
   execution, feedback or verification. Name the next consumer, the expected benefit and
   a bounded comparison with the existing method.
2. **Develop a candidate.** Select the appropriate entity type, improve an existing suitable
   component where possible, and preserve baseline observations before registration.
3. **Evaluate the version.** Register through structural admission, then compare behavior
   using real calls. Tie the judgment to the candidate version and retained evidence.
4. **Decide and reuse.** Keep a passing version, or explicitly roll back/unload it. Exercise
   retained improvements in their intended operation and inspect the resulting work.

A first correction, a successful verification or an unfamiliar integration can reveal a
useful opportunity. Repeated failure is not required. The agent still needs evidence, a
concrete consumer and enough budget to evaluate the change and finish the task. Product
edits and component counts alone do not demonstrate capability improvement.

Tasks that explicitly require verified evolution also check version-specific evaluation and
post-adoption use receipts before reporting success. A failed comparison or the absence of a
credible opportunity remains an unmet outcome; it does not justify inventing a successful result.

### Extensions and versions

- Built-in capabilities live in the framework package; evolution artifacts live under external `extension/<type>/` or session staging.
- `ExtensionManager` handles registration, admission, version archives and rollback.
- An existing component with `enable_evolving=False` cannot be overwritten through evolution.
- Gateway sessions stage changes separately and require explicit promotion into shared extensions.
- Registration, functional evaluation, promotion and later use are distinct states, visible through their records.

See the [extension contract](agentevolver/extension/README.md) for evidence requirements and activation boundaries.

## From task trajectories to end-to-end training

`trace` retains execution observations; `TrajectoryHook` projects the run into a sequence of
training-oriented steps:

```text
s_t = (z_t, a_t, o_t, r_t)

z_t  effective context sent to the model
a_t  model reasoning and native tool calls
o_t  action results or errors
r_t  reward backfilled by a benchmark or evaluator
```

Records preserve task identity, outcome, parent/subtask relationships and token usage. They persist
to `<log_root>/trajectory/<task_id>.jsonl` and export through `export_sft()` or the pluggable
`RLFormat` interface. The built-in VERL format provides text-level episodes; the training provider
owns tokenization and masks.

The available path is **real task → reward-annotated trajectory → SFT records / RL episodes**.
Training execution, model/checkpoint management, model evaluation and serving feedback remain
integration work. Component evolution and training-data export do not imply that an ordinary task
updates model weights. See the [trajectory contract](agentevolver/trajectory/README.md).

## Web workbench

<div align="center">

<a href="https://dvampire.github.io/AgentEvolver/ui.html?lang=en"><img src="docs/assets/ui/workbench-overview.png" width="100%" alt="AgentEvolver project overview with Plan, Runtime, eight evolvable entity types and project files"></a>

**[Watch the 11-part feature tour](https://dvampire.github.io/AgentEvolver/ui.html?lang=en)**

</div>

Five views share one project and Gateway:

| View | What it is for |
| --- | --- |
| **Overview** | Read the plan summary and details; inspect Runtime activity, eight entity types, staged candidates, shared notes and files |
| **Chat** | Submit tasks, attach files, follow activity, inspect steps, answer approval requests and control running work |
| **Canvas** | Compose JSON flows visually and execute them on the shared Workflow Runtime |
| **Code** | Open the project workspace in browser-based VS Code |
| **Science** | Use the same project kernel as the agent's code interpreter, inspect outputs and compute state, or continue in JupyterLab |

A searchable project picker keeps sessions accessible. The sidebar also opens capability and model
catalogs, Browser/Computer live views, configured remote machines, and connection settings.
Candidate staging is displayed separately from evidence of functional improvement.

The screenshot and eleven clips use a prepared sample project on a live Gateway, recorded on
September 16, 2026. Science executes a real Python cell on synthetic data; the sample Runtime is
idle. These are interface demonstrations. See the [recording guide](docs/assets/ui/README.md).

Code requires Docker and the locally built base image; its editor image builds on first use.
Science requires JupyterLab and ipykernel in the Gateway's Python environment. Setup instructions
are in the [frontend guide](frontend/README.md). Canvas JSON flows have their own library;
agent-authored HTML workflows are a separate interface described in the [Canvas guide](docs/canvas.md).

## Safety, budgets, and observability

### Security boundaries

| Layer | Responsibility |
| --- | --- |
| Sandbox | Isolate code, browser, or desktop environments; backends provide different capability and isolation levels |
| Network policy | Apply the selected backend's egress controls; container relay policies mediate and record permitted outbound requests |
| Permission | Classify read, write, destructive, network, process, package-management, and related intent before Tool or Sandbox execution |
| Lifecycle | A write-ahead container ledger cleans leaked resources; a shared port registry reduces service conflicts |

“Supports sandboxing” does not mean “safe for every high-risk workload.” Deployers must still review
images, mounts, credentials, network allowlists, host Docker-socket access, and permission modes against
their threat model.

### Budgets and records

- `constraint/` tracks step, token, and wall-time budgets and renders the remaining budget into agent context;
- `trace/` persists structured events and streams them through the Gateway;
- `trajectory/` projects runs into reward-annotated step records and exports OpenAI Chat SFT or RL formats such as VERL;
- [Session records](agentevolver/session/README.md) let an agent search earlier runs and read the steps around a matching result;
- `memory/` maintains recent history, compacted working memory, todos, call paths, and final results;
- `tool/spill/` writes an oversized tool result to a file whole and puts the locator in the excerpt, so the part that did not fit can still be read;
- `benchmark/` provides entry points for AIME, GPQA, GSM8K, HLE, LeetCode, DeepWeb, ProgramBench, and related evaluations.

The prompt separates a stable instruction/catalog prefix from changing task state to support
provider prompt caching. Cache hits and savings depend on the model route and workload; inspect
reported usage in the run records. Context construction is described in the
[agent guide](agentevolver/agent/README.md).

## Extending the framework

Most component modules follow the same shape:

```text
agentevolver/<module>/
├── default/       # hand-written built-ins
├── types.py       # base classes, data structures, contracts
├── context.py     # registry and lifecycle, where applicable
├── server.py      # <module>_manager facade
└── README.md      # module boundary and usage guide
```

A new hand-written component uses the matching registry decorator and is exported from
`default/__init__.py`. An evolved component does not edit package `__init__.py` files; it is written to
`extension/` and loaded through directory scanning and ExtensionManager.

| Component | Entry point |
| --- | --- |
| Agent / Prompt | `agentevolver.agent` / `agentevolver.prompt` |
| Tool / Skill | `agentevolver.tool` / `agentevolver.skill` |
| Environment / Sandbox | `agentevolver.environment` / `agentevolver.sandbox` |
| Memory / Hook / Constraint | `agentevolver.memory` / `agentevolver.hook` / `agentevolver.constraint` |
| Dataset / Benchmark | `agentevolver.data` / `agentevolver.benchmark` |
| Connector | discovered from `CONNECTOR.md`, managed by `connector_manager` |
| Workflow | compiled from HTML by `WorkflowCompiler` and executed on the shared runtime |
| Plugin | `plugin.py` + `PLUGIN.md`, managed by `plugin_manager` |

Each module's own `README.md` is its contract: what it owns, its shape, and the rules for
extending it.

## Repository and output layout

```text
AgentEvolver/
├── agentevolver/       # framework core and built-in capabilities
├── configs/            # runtime configurations
├── extension/          # shared, versioned evolved components
├── frontend/           # React/Vite Web UI and terminal client
├── examples/           # agent entry points and task examples
├── docs/               # website, UI tour, and focused guides
├── docker/             # base, browser, desktop, and related images
├── datasets/           # local-first benchmark data
├── scripts/            # installation, launch, and maintenance scripts
├── tests/              # fast and integration tests
└── output/             # sessions, logs, workspaces, and runtime state (generated)
```

Framework writes are resolved centrally through `agentevolver.paths`. The main writable roots are
`output/` and `extension/`; relocate them with `AGENTEVOLVER_HOME` and
`AGENTEVOLVER_EXTENSION_ROOT`.

## Documentation map

| Document | Contents |
| --- | --- |
| [Website](https://dvampire.github.io/AgentEvolver/) | Positioning, architecture, characteristics, trade-offs, and quick start |
| [Complete tutorial](https://dvampire.github.io/AgentEvolver/tutorial.html) | Thirteen chapters: mental model, installation, entry points, first run, output tree, extensions, SFT/RL trajectory export, Web UI, safety, evolution, and troubleshooting |
| [Architecture guide](https://dvampire.github.io/AgentEvolver/architecture.html) | Runtime boundaries, event log projections, extension lifecycle, and the training-data flywheel |
| [Module reference](https://dvampire.github.io/AgentEvolver/modules.html) | Searchable guide to module responsibilities, runtime placement, public API, and source |
| [Web UI tour](https://dvampire.github.io/AgentEvolver/ui.html) | Eleven short clips showing the workbench feature by feature |
| [Contributor guide](https://dvampire.github.io/AgentEvolver/development.html) | Module contracts, verification gates, invariants, and safe extension patterns |
| [`scripts/INSTALL.md`](scripts/INSTALL.md) | Installation, optional extras, Vault, and environment setup |
| [`frontend/README.md`](frontend/README.md) | Gateway and Web UI development and deployment |
| [Factor and strategy mining demo](docs/demos/factor_strategy_mining.md) | One researcher develops a market connector and two backtesting environments, with one continuous research report and a frozen final-test protocol |
| [`docs/workflows.md`](docs/workflows.md) | Dynamic HTML workflows |
| [`docs/canvas.md`](docs/canvas.md) | Visual Canvas flows |
| [`docs/capability-schemas.md`](docs/capability-schemas.md) | Capability schema protocol |
| [`docs/tool-catalog.md`](docs/tool-catalog.md) | Generated: every registered tool, its parameters and its permission mode |
| [`agentevolver/trajectory/README.md`](agentevolver/trajectory/README.md) | Trajectory capture, persistence, and SFT/RL export contract |

## Project status

AgentEvolver is currently a `0.1.0` research and engineering framework. Component evolution and the
SFT/RL trajectory data interface are implemented; training execution, checkpoint/model version
management, and feedback of trained models into serving are the next-stage roadmap. Before production
or high-risk use, establish task-specific evaluations, approval flows, security policy, and rollback
drills.

## License

[MIT](LICENSE) © 2026 Wentao Zhang
