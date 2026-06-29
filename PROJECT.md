
# AgentEvolver

A self-evolving multi-agent framework. A MetaAgent orchestrates sub-agents to complete user tasks, while optimizer/evaluator/generator agents continuously improve the tool ecosystem.

## Directory Structure

```
AgentEvolver/
├── src/                    # Core framework source
│   ├── agent/              # All agent implementations
│   │   ├── actor/          # MetaAgent, CodeAgent, ReasonActAgent
│   │   ├── optimizer/      # Agents that evolve existing source code
│   │   ├── evaluator/      # Agents that assess quality
│   │   ├── generator/      # Agents that create new agents/tools/skills
│   │   ├── extended/       # Generated agents (auto-created, do not edit manually)
│   │   ├── types.py        # Base Agent class, AgentContext, AgentResponse
│   │   └── server.py       # AgentManagerServer (agent_manager singleton)
│   ├── tool/               # Tool implementations
│   │   ├── default/        # Built-in tools (bash, read/write/edit file, git, done, ...)
│   │   ├── extended/       # Generated/evolved tools
│   │   ├── workflow/       # Workflow tools (todo)
│   │   ├── types.py        # Base Tool class, ToolResponse
│   │   └── server.py       # ToolManagerServer (tool_manager singleton)
│   ├── prompt/             # Prompt templates (one per agent)
│   │   ├── default/        # Built-in HTML prompts
│   │   └── extended/       # Generated prompts (tool-calling agents)
│   ├── skill/              # Skills (reusable multi-step SOP workflows)
│   │   ├── default/        # Built-in skills (generate_agent_skill, ...)
│   │   └── extended/       # Generated skills
│   ├── environment/        # Execution environments (browser, sandbox, ...)
│   │   ├── default/        # Built-in environments
│   │   ├── extended/       # Generated environments
│   │   ├── sandbox.py      # Docker-backed sandbox (opensandbox-server)
│   │   ├── types.py        # Base Environment class, EnvironmentContext
│   │   └── server.py       # environment_manager singleton
│   ├── benchmark/          # Benchmarks
│   │   ├── default/        # Built-in benchmarks (aime24/25, gpqa, gsm8k, hle,
│   │   │                   #   leetcode, deepweb, programbench)
│   │   ├── extended/       # Generated benchmarks
│   │   ├── types.py        # Base Benchmark/Task/Stats, llm_judge helper
│   │   ├── server.py       # benchmark_manager singleton
│   │   └── utils.py        # clean_text + ensure_dataset (datasets-first/HF download)
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
- **`default/` vs `extended/`**: Modules that the framework can evolve (`agent`, `tool`, `prompt`, `skill`, `environment`, `benchmark`) split into `default/` (built-in, hand-written) and `extended/` (generated/evolved). `constraint`, `hook`, and `memory` currently ship `default/` only.
- **Registries**: Evolvable components self-register with an mmengine `Registry` (in `src/registry.py`) via a class decorator, e.g. `@TOOL.register_module()`. The registry then resolves the class by name from config.

## Conventions

Follow these rules when adding or generating code so the framework can discover and evolve it.

1. **Put generated files in the corresponding `extended/` folder.** Any newly generated component goes under its module's `extended/` directory — `src/tool/extended/`, `src/agent/extended/`, `src/prompt/extended/`, `src/skill/extended/`, `src/environment/extended/`, `src/benchmark/extended/`. Never edit `extended/` files by hand; they are auto-managed. Hand-written built-ins go in `default/` (or, for still-flat modules like `data`, directly in the module root).

2. **Export the new class from the package `__init__.py`.** After creating a class, add it to the relevant `__init__.py` (both the `from .x import Y` import and the `__all__` list) — for an extended component, the `extended/__init__.py`; for a flat-module benchmark/dataset, the module's top-level `__init__.py`. A class that is not exported will not be importable or discoverable.

3. **Register with the right Registry.** Decorate the class with the matching registry decorator so it can be built from config by name (see the table below).

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
