# Website evolution scenarios

Four open product briefs explore different kinds of rich, useful browser experiences.
Directory names match each product's identity and purpose; use the paths below in launch commands.

| Directory | Working title | Product intent |
| --- | --- | --- |
| `arkbound_game/` | ECHO — Arkbound | A visually striking 3D ocean exploration game with meaningful voyages and player agency. |
| `commonspace_forum/` | COMMONSPACE — A living forum | A beautiful shared community where visitors discover, contribute to and revisit real conversations. |
| `lumen_museum/` | LUMEN — Museum of AI & Technology | An educational museum where manipulating exhibits helps visitors understand real technological ideas. |
| `orbital_simulator/` | ORBITAL — A living universe | A grand 3D journey through many galaxies, the Milky Way and the Solar System, with explained reference frames and physically grounded motion. |

Each directory includes a complete, matching set of inputs:

- `scenario.html` — product brief visible to the Website Builder;
- `persona_01.html` through `persona_03.html` — private visitor contexts routed one-to-one to Website User Agents.

## Design freedom and quality

The briefs establish purpose, meaningful interaction, visual ambition and observable evidence.
They leave the Builder room to choose branding, composition, architecture, content, mechanics
and an appropriate first-release scope. Suggested features and themes are possibilities, not
checklists. Working titles are starting points, not mandatory branding.

The central promises remain concrete: the game must be playable, forum contributions must
reach independent visitors and persist, museum interactions must teach something real, and
simulator inputs must affect an explained model. Attractive screenshots alone are insufficient;
the distinctive presentation must survive ordinary use.

Personas describe motivations, curiosity and tensions. They do not prescribe findings, demand
named features or require dissatisfaction. Users should try the product actually delivered,
separate observations from ideas, and follow an unresolved question across versions. Their
feedback can justify a new interaction or a change to the original design. There is no fixed
feature roadmap or quota of new requests.

Each brief includes optional horizons for new experiences, such as media participation,
public information or reconstructable experiments. These leave room for unfamiliar integrations
without prescribing providers or a capability-generation checklist. Users describe the experience
they want; the Builder investigates feasibility and chooses how to deliver it.

## Running and validating

`arkbound_game/` remains the launcher's default. Select another complete scenario with:

```bash
python examples/run_website_evolution_demo.py \
  --scenario-dir examples/tasks/website_evolution/lumen_museum
```

Check parsing, role routing and configuration without starting models or browsers:

```bash
python examples/run_website_evolution_demo.py \
  --scenario-dir examples/tasks/website_evolution/lumen_museum \
  --validate-only
```

Keep scenario-specific inputs together here instead of mixing briefs and personas from different
products. Existing runs retain their staged inputs; a rewritten brief applies to a new run.

## Product feedback and capability evolution

The launcher owns release cadence and private participant routing. The Builder's shared prompt
owns planning, delivery and independent preview acceptance. Scenario briefs define product
outcomes; they do not prescribe tools to evolve, components to generate or an Agent architecture.

The shared `agentevolver/prompt/module/evolution_rules.html` policy identifies capability
opportunities from actual failures, corrections, feedback, measured quality and repeated costs.
It also covers a chosen new experience: identify the operation it needs, inspect existing
capabilities and documentation, and run a small authorized probe when useful. A concrete reusable
gap or better method can justify generation or optimization before a failure occurs. The normal
evaluation, adoption and actual-consumer verification requirements still apply; a product feature
or an API wrapper alone is not evidence of successful evolution.
`self_evolving_skill` describes investigation, comparison and adoption. No qualifying evidence
is a valid outcome. Report product improvements and verified capability improvements separately.

Co-design happens through Agent conversations and participant results. The forum naturally
includes its own community discussions; it does not become a development request tracker.

Participant memory and browser storage are separate. Each conversation turn currently starts
a fresh browser context. Test same-browser persistence by reloading within a turn. Test server
persistence or shared experiment reconstruction through the product's actual identity, URL or
import flow when it promises those behaviours. Remembering an earlier visit does not prove its
local browser state survived.
