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
