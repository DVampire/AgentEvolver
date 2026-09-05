# Website evolution scenarios

Each subdirectory is one self-contained scenario. The launcher reads these conventional names:

- `scenario.html` — requirements visible to the Website Builder;
- `persona_01.html` through `persona_03.html` — private contexts routed one-to-one to Website User Agents.

`echo_ark/` is the default complete scenario. `community_learning/` is the earlier complete
example. `dream_museum/` and `pulse_city/` currently provide scenario briefs only; supply three
persona files before selecting either directory for a run.

Run a complete scenario with:

```bash
python examples/run_website_evolution_demo.py \
  --scenario-dir examples/tasks/website_evolution/echo_ark
```

Keep scenario-specific inputs here rather than under `examples/inputs/`; this prevents a brief
from being accidentally paired with personas belonging to another product.

## Product tasks and autonomous capability evolution

Scenario briefs define product outcomes, constraints, and observable acceptance. Persona files
define private goals and preferences, not prescribed findings or component requests. Neither
should ask the Builder to evolve, name a component to generate, or require coverage of eight types.
The launcher's iteration count describes product releases, not capability changes.

The shared `agentevolver/prompt/module/evolution_rules.html` system policy detects opportunities
from actual operation failures, corrections, feedback, measured quality, and repeated costs.
`self_evolving_skill` describes the resulting investigation, comparison, and adoption lifecycle.
No qualifying evidence is a valid outcome. Report product customization and verified capability
improvement separately; registering a component alone proves neither use nor improvement.

ECHO centers on resident/developer dialogue, scoped proposals, confirmation, continuity, and undo.
Richer media and broader shared-world features follow real use rather than dominating the first
release. Other scenarios may expose different needs; a single run need not use every component type.
