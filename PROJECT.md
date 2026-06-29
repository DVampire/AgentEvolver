
# AgentEvolver

A self-evolving multi-agent framework. A MetaAgent orchestrates sub-agents to complete user tasks, while optimizer/evaluator/generator agents continuously improve the tool ecosystem.

## Directory Structure

```
AgentEvolver/
├── src/                    # Core framework source
│   ├── agent/              # All agent implementations (built-ins only; evolved → extension/)
│   │   ├── actor/          # MetaAgent, CodeAgent, ReasonActAgent
│   │   ├── optimizer/      # Agents that evolve existing source code
│   │   ├── evaluator/      # Agents that assess quality
│   │   ├── generator/      # Agents that create new agents/tools/skills
│   │   ├── types.py        # Base Agent class, AgentContext, AgentResponse
│   │   └── server.py       # AgentManagerServer (agent_manager singleton)
│   ├── tool/               # Tool implementations
│   │   ├── default/        # Built-in tools (bash, read/write/edit file, git, done, ...)
│   │   ├── workflow/       # Workflow tools (todo)
│   │   ├── types.py        # Base Tool class, ToolResponse
│   │   └── server.py       # ToolManagerServer (tool_manager singleton)
│   ├── prompt/             # Prompt templates (one per agent)
│   │   └── default/        # Built-in HTML prompts
│   ├── skill/              # Skills (reusable multi-step SOP workflows)
│   │   └── default/        # Built-in skills (generate_agent_skill, ...)
│   ├── environment/        # Execution environments (browser, sandbox, ...)
│   │   ├── default/        # Built-in environments
│   │   ├── sandbox.py      # Docker-backed sandbox (opensandbox-server)
│   │   ├── types.py        # Base Environment class, EnvironmentContext
│   │   └── server.py       # environment_manager singleton
│   ├── benchmark/          # Benchmarks (stay in src; not hot-pluggable)
│   │   ├── default/        # Built-in benchmarks (aime24/25, gpqa, gsm8k, hle,
│   │   │                   #   leetcode, deepweb, programbench)
│   │   ├── types.py        # Base Benchmark/Task/Stats, llm_judge helper
│   │   ├── server.py       # benchmark_manager singleton
│   │   └── utils.py        # clean_text + ensure_dataset (datasets-first/HF download)
│   ├── extension/          # ExtensionManager — loads/evolves the external extension/ tree
│   │   ├── types.py        # Manifest, ManifestComponent
│   │   └── server.py       # extension_manager singleton
│   ├── data/               # Dataset loaders (DATASET registry, one per dataset)
│   ├── constraint/         # Run constraints (step/token/wall-time budgets)
│   │   ├── default/        # Built-in constraints
│   │   ├── types.py        # Base Constraint, ConstraintContext, ConstraintStatus
│   │   └── server.py       # constraint_manager singleton
│   ├── memory/             # Memory systems
│   │   ├── default/        # Built-in memory systems (tiered, ...)
│   │   ├── types.py / server.py
│   ├── hook/               # Hook pipeline (registration, memory, trace, ...)
│   │   ├── default/        # Built-in hooks (compact, ...)
│   │   ├── types.py / server.py
│   ├── model/              # LLM client (model_manager singleton)
│   ├── task/               # Task types and TaskManager
│   ├── trace/              # Trace/observability (TraceManager, UI server)
│   ├── runtime/            # Agent runtime: mailbox + pump + lifecycle
│   ├── permission/         # Permission modes and operation gating
│   ├── version/            # Version tracking for tools, agents, prompts, skills
│   ├── config/             # Config loading (mmengine-based)
│   ├── dynamic/            # Dynamic class loading for evolved code
│   ├── session/            # Session context and isolation
│   ├── queue/              # AsyncQueue primitive
│   ├── response/           # Response / ResponseType types
│   ├── message/            # Message types (SystemMessage, HumanMessage, ...)
│   ├── visual/             # Visual assets (CSS, templates, rendering helpers)
│   ├── logger/             # Logging
│   ├── utils/              # Shared utilities
│   └── registry.py         # mmengine Registry instances (see Registries below)
├── configs/                # mmengine config files
│   ├── base.py             # Shared defaults (window_size, max_tokens, ...)
│   ├── meta_agent.py       # Example: full config for running MetaAgent
│   ├── agents/             # Per-agent config fragments
│   ├── tools/              # Per-tool config fragments
│   └── memory/             # Memory system config fragments
├── datasets/               # Vendored benchmark datasets (deepweb-bench, ...)
├── extension/              # Hot-pluggable evolved content (OUTSIDE src/, loaded by ExtensionManager)
│   ├── manifest.json       # active set: component -> active version + file (git-ignored)
│   ├── tool/<name>.py      # active source — flat, normal paths
│   ├── agent/<name>.py
│   ├── prompt/<name>.html
│   ├── skill/<name>/SKILL.md
│   ├── environment/<name>.py
│   └── .versions/<module>/<name>/<version>.<ext>   # version archive (git-ignored)
├── tests/                  # Unit and integration tests
├── examples/               # Runnable entry-point scripts
│   └── run_meta_agent.py   # Main entry point — MetaAgent orchestrates everything
├── scripts/                # Install / setup scripts (INSTALL.md, requirements.txt)
├── others/                 # Scratch / research notes (not part of the framework)
└── work_dir/               # Per-run scratch output (git-ignored)
```

## Key Concepts

- **`{{ project_root }}`**: Absolute path to the repo root. Always use it to construct source file paths; never use relative paths.
- **`{{ work_dir }}`**: Per-run scratch directory for temporary files. Do not write source code here.
- **Built-ins vs extensions**: Hand-written built-ins live in each module's `default/` folder inside `src/` (e.g. `src/tool/default/`). Generated/evolved components live OUTSIDE `src/`, in the external `extension/` tree, and are loaded at runtime by the **ExtensionManager**. `src/` stays immutable; `extension/` is mutable evolved content.
- **Hot-plug / ExtensionManager** (`src/extension/`): On startup, after the component managers load their built-ins, `extension_manager.initialize()` layers the active extension set on top. Authoring writes a flat active file (`extension/<module>/<name>.py`); `extension_manager.add_component(...)` registers it via the owning `*_manager`, archives the version under `extension/.versions/`, and records the active version in `extension/manifest.json`. Multiple versions of a component coexist in `.versions/`; `extension_manager.rollback(module, name, version)` restores any of them. There is **no `__init__.py` to edit** for extensions — loading is by directory scan + dynamic import.
- **Registries**: Components self-register with an mmengine `Registry` (in `src/registry.py`) via a class decorator, e.g. `@TOOL.register_module()`. Built-ins register at import time; extensions are registered at runtime by the ExtensionManager (which loads the class via `dynamic_manager` and calls `<module>_manager.register`).

## Conventions

Follow these rules when adding or generating code so the framework can discover and evolve it.

1. **Generated/evolved components go in the external `extension/` tree — never in `src/`.** Write the flat active file: `extension/tool/<name>.py`, `extension/agent/<name>.py` (+ `extension/prompt/<name>.html`), `extension/skill/<name>/SKILL.md`, `extension/environment/<name>.py`. The ExtensionManager registers it and archives the version automatically. **Do NOT edit any `__init__.py`** for extensions. Hand-written built-ins (shipped with the framework) still go in the module's `src/<module>/default/` folder.

2. **Built-ins are exported from `default/__init__.py`; extensions are not.** A new hand-written built-in must be imported in its module's `default/__init__.py` (import + `__all__`) so it registers at import time. Extension components are discovered by directory scan, so they need no `__init__.py` entry.

3. **Register with the right Registry.** Decorate the class with the matching registry decorator (see the table below). Built-ins register on import; extensions are registered at runtime by the ExtensionManager via the same registries.

4. **Keep the module's `types.py` / `server.py` contract.** Subclass the base class in `types.py` and implement its abstract methods; do not bypass the module's `*_manager` singleton in `server.py`.

5. **Benchmarks read data from `datasets/` first, then download from HuggingFace.** Every benchmark stores its data under `datasets/<name>/`. A benchmark declares an `hf_repo_id` field and, in `initialize()`, calls `ensure_dataset(<name>, self.hf_repo_id)` (in `src/benchmark/utils.py`) before loading: if `datasets/<name>/` is missing/empty it is snapshot-downloaded from HuggingFace, otherwise the local copy is used. Set the `HF_ENDPOINT` env var to use a mirror. Both `hf_repo_id` and `path` are config-overridable.

### Registries (`src/registry.py`)

| Registry          | Locations         | Decorator                              |
| ----------------- | ----------------- | -------------------------------------- |
| `TOOL`            | `src.tool`        | `@TOOL.register_module()`              |
| `AGENT`           | `src.agent`       | `@AGENT.register_module()`             |
| `PROMPT`          | `src.prompt`      | `@PROMPT.register_module()`            |
| `DATASET`         | `src.data`        | `@DATASET.register_module()`           |
| `BENCHMARK`       | `src.benchmark`   | `@BENCHMARK.register_module()`         |
| `SKILL`           | `src.skill`       | `@SKILL.register_module()`             |
| `HOOK`            | `src.hook`        | `@HOOK.register_module()`              |
| `CONSTRAINT`      | `src.constraint`  | `@CONSTRAINT.register_module()`        |
| `ENVIRONMENT`     | `src.environment` | `@ENVIRONMENT.register_module()`       |
| `MEMORY_SYSTEM`   | `src.memory`      | `@MEMORY_SYSTEM.register_module()`     |
