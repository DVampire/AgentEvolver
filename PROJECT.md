
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
│   ├── prompt/
│   │   ├── default/        # Built-in HTML prompts, one per agent
│   │   └── extended/       # Generated prompts (tool-calling agents)
│   ├── skill/              # Skills (reusable multi-step SOP workflows)
│   │   ├── default/        # Built-in skills (generate_agent_skill, ...)
│   │   └── extended/       # Generated skills
│   ├── memory/             # Memory systems
│   ├── model/              # LLM client (model_manager singleton)
│   ├── hook/               # Hook pipeline (registration, memory, trace, ...)
│   ├── task/               # Task types and TaskManager
│   ├── trace/              # Trace/observability (TraceManager, UI server)
│   ├── version/            # Version tracking for tools, agents, prompts, skills
│   ├── config/             # Config loading (mmengine-based)
│   ├── dynamic/            # Dynamic class loading for evolved code
│   ├── session/            # Session context and isolation
│   ├── message/            # Message types (SystemMessage, HumanMessage, ...)
│   ├── logger/             # Logging
│   └── utils/              # Shared utilities
├── configs/                # mmengine config files
│   ├── base.py             # Shared defaults (window_size, max_tokens, ...)
│   ├── meta_agent.py       # Example: full config for running MetaAgent
│   ├── agents/             # Per-agent config fragments
│   ├── tools/              # Per-tool config fragments
│   └── memory/             # Memory system config fragments
├── tests/                  # Unit and integration tests
└── examples/               # Runnable entry-point scripts
    └── run_meta_agent.py   # Main entry point — MetaAgent orchestrates everything
```

## Key Concepts

- **`{{ project_root }}`**: Absolute path to the repo root. Always use it to construct source file paths; never use relative paths.
- **`{{ work_dir }}`**: Per-run scratch directory for temporary files. Do not write source code here.
- New tools added under `src/tool/extended/` must also be exported from `extended/__init__.py`.
