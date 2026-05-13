
# AgentEvolver

A self-evolving multi-agent framework. A MetaAgent orchestrates sub-agents to complete user tasks, while optimizer/evaluator/generator agents continuously improve the tool ecosystem.

## Directory Structure

```
AgentEvolver/
├── src/                    # Core framework source
│   ├── agent/              # All agent implementations
│   │   ├── actor/          # MetaAgent, CodeAgent, ReasonActAgent
│   │   ├── optimizer/      # Agents that evolve existing tool source code (e.g. ToolOptimizeAgent)
│   │   ├── evaluator/      # Agents that assess tool quality (e.g. ToolEvaluateAgent)
│   │   ├── generator/      # Agents that create new tools from descriptions (e.g. ToolGenerateAgent)
│   │   ├── types.py        # Base Agent class, AgentContext, AgentResponse
│   │   └── server.py       # AgentManagerServer (agent_manager singleton)
│   ├── tool/               # Tool implementations
│   │   ├── default/        # Built-in tools (bash, read/write/edit file, git, done, ...)
│   │   ├── extended/       # User-generated/evolved tools (hello_world, ...)
│   │   ├── workflow/       # Workflow tools (todo)
│   │   ├── types.py        # Base Tool class, ToolResponse
│   │   └── server.py       # ToolManagerServer (tool_manager singleton)
│   ├── prompt/
│   │   └── default/        # Prompt markdown files, one per agent (meta_agent.md, ...)
│   ├── memory/             # Memory systems (general_memory_system)
│   ├── model/              # LLM client (model_manager singleton)
│   ├── hook/               # Hook pipeline (pre/post step, escalation, token limits)
│   ├── skill/              # Skills (reusable multi-step workflows)
│   ├── task/               # Task types and TaskManager
│   ├── trace/              # Trace/observability (TraceManager, UI server)
│   ├── version/            # Version tracking for tools and agents
│   ├── config/             # Config loading (mmengine-based)
│   ├── dynamic/            # Dynamic class loading for evolved code
│   ├── session/            # Session context and isolation
│   ├── message/            # Message types (SystemMessage, HumanMessage, ...)
│   ├── logger/             # Logging
│   └── utils/              # Shared utilities
├── configs/                # mmengine config files
│   ├── base.py             # Shared defaults (window_size, max_tokens, ...)
│   ├── agents/             # Per-agent config fragments
│   ├── tools/              # Per-tool config fragments
│   ├── memory/             # Memory system config fragments
│   ├── meta_agent.py       # Full config for running MetaAgent
│   ├── tool_optimize_agent.py
│   ├── tool_evaluate_agent.py
│   └── tool_generate_agent.py
├── tests/                  # Unit and integration tests
└── examples/               # Runnable entry-point scripts
    ├── run_meta_agent.py
    ├── run_code_agent.py
    ├── run_reason_act_agent.py
    ├── run_tool_optimize_agent.py
    ├── run_tool_evaluate_agent.py
    └── run_tool_generate_agent.py
```

## Key Concepts

- **`{{ project_root }}`**: The **absolute path** to the AgentEvolver repo root. Always use it to construct source file paths (e.g. `{{ project_root }}/src/tool/extended/my_tool.py`). Never use relative paths.
- **`{{ workdir }}`**: A per-run scratch directory for temporary files (logs, intermediate outputs, plan files). Do **not** write source code here — all source changes must go under `{{ project_root }}/src/`.
- **Tools**: `{{ project_root }}/src/tool/`. Built-in tools in `default/`, generated/evolved tools in `extended/` (must also be exported from `extended/__init__.py`).
- **Agents**: `{{ project_root }}/src/agent/`. Actor agents in `actor/`, optimizer in `optimizer/`, evaluator in `evaluator/`, generator in `generator/`.
- **Prompts**: `{{ project_root }}/src/prompt/default/`. One `.md` file per agent, rendered with variables at call time.
- **Skills**: `{{ project_root }}/src/skill/default/`. Reusable multi-step workflows, each in its own subdirectory with a `SKILL.md`.
- **Configs**: `{{ project_root }}/configs/`. Top-level configs assemble agent/tool/memory fragments via `mmengine.read_base`.
- **Examples**: `{{ project_root }}/examples/`. Entry-point scripts for running each agent standalone.
- **Tests**: `{{ project_root }}/tests/`. Unit and integration tests.
