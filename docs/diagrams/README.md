# Architecture diagram sources

The English and Chinese diagrams are native PowerPoint drawings: editable text boxes,
shapes and connectors, with the same layout and component relationships in both languages.
They contain no screenshot or AI-generated illustration. The homepage, architecture guide
and repository READMEs use their static SVG exports.

The 16:9 logical diagram keeps the original spatial structure: the user, task and
orchestrator across the top; actor agents on the left; runtime and communication in
the center; the capability ecosystem below; the evolution cycle, extension manager
and external world on the right; supporting infrastructure along the bottom.

The root box includes MetaAgent and Builder roles, with optional actor delegation.
The evolution ring is supported by `self_evolving_skill`, the acting agent's self-review
and `adoption_tool`. Runtime/Protocol names the process and message contracts; it does
not imply a separate `protocol/` package. Plan/Context and Deploy/Gateway remain within
the infrastructure band. Godot appears with environments and external interaction.

| Language | Editable source | Web export | Raster fallback |
| --- | --- | --- | --- |
| English | [arch.pptx](../assets/arch.pptx) | [arch.svg](../assets/arch.svg) | [arch.png](../assets/arch.png) |
| Chinese | [arch_zh.pptx](../assets/arch_zh.pptx) | [arch_zh.svg](../assets/arch_zh.svg) | [arch_zh.png](../assets/arch_zh.png) |

## Regenerate

The presentation is authored by [build_architecture.py](build_architecture.py), using
[python-pptx's native shape API](https://python-pptx.readthedocs.io/en/latest/api/shapes.html).
Edit its paired labels and shared geometry, or edit the decks in PowerPoint and keep the
source script in sync. The generator replaces both decks when rerun.

Use a separate documentation environment; these are not runtime dependencies:

```bash
python -m venv /tmp/agentevolver-diagrams
/tmp/agentevolver-diagrams/bin/pip install python-pptx==1.0.2 PyMuPDF==1.26.7
/tmp/agentevolver-diagrams/bin/python docs/diagrams/build_architecture.py
```

Install DejaVu Sans and Noto Sans CJK SC on the machine performing the office export.
The latter is available from the [Noto CJK project](https://github.com/notofonts/noto-cjk).
Keep each language in a one-slide deck. With LibreOffice Impress available:

```bash
mkdir -p /tmp/agentevolver-diagram-pdf
libreoffice -env:UserInstallation=file:///tmp/agentevolver-diagram-profile \
  --headless --convert-to pdf:impress_pdf_Export \
  --outdir /tmp/agentevolver-diagram-pdf \
  docs/assets/arch.pptx docs/assets/arch_zh.pptx
/tmp/agentevolver-diagrams/bin/python docs/diagrams/export_architecture.py \
  /tmp/agentevolver-diagram-pdf
```

The checked-in exports use LibreOffice 24.2.7.2. The PDF is an intermediate rendering of
the PPTX; [export_architecture.py](export_architecture.py) converts its vector drawing to
SVG with outlined glyphs and also refreshes the PNG fallback. This preserves Chinese
text without requiring website visitors to install the font. The editable text remains
in PowerPoint. The exporter refuses raster images, scripts and foreign objects in the SVG.

A direct Impress SVG export can include slideshow scripting and initially hidden content.
The PDF-to-static-SVG path avoids depending on scripts inside an HTML image. The PowerPoint
source disables theme shadows so the office renderer does not rasterize those effects.

## Source map

The drawing groups runtime behavior; it is not an exhaustive import-dependency graph.
Review these owners when changing a box or connector:

| Diagram area | Implementation |
| --- | --- |
| Task entry and manifest binding | [shared launcher](../../examples/run_meta_agent.py), [Agent.prepare_task](../../agentevolver/agent/loop/agent.py), [task context](../../agentevolver/task/context.py) |
| Agent processes, optional children, messages and cleanup | [runtime kernel](../../agentevolver/runtime/kernel.py), [process and run budget](../../agentevolver/runtime/process.py) |
| Configured roles and the shared loop | [MetaAgent](../../agentevolver/agent/actor/meta_agent.py), [WebsiteBuilder](../../agentevolver/agent/actor/website_builder_agent.py), [GameBuilder](../../agentevolver/agent/actor/game_builder_agent.py) |
| Four context layers and compaction | [assembler](../../agentevolver/agent/context/assembler.py), [conversation](../../agentevolver/agent/context/conversation.py) |
| Scoped capability dispatch | [router](../../agentevolver/agent/loop/router.py), [executor](../../agentevolver/agent/loop/executor.py) |
| Plan index and detailed records | [plan manager](../../agentevolver/plan/server.py) |
| Environment interaction | [environment contract](../../agentevolver/environment/types.py), [Godot environment](../../agentevolver/environment/default/godot/environment.py) |
| Websites, game previews and versioned URLs | [deploy manager](../../agentevolver/deploy/server.py), [Gateway](../../agentevolver/gateway/README.md) |
| Evolution policy and version-scoped evidence | [shared rules](../../agentevolver/prompt/module/evolution_rules.html), [adoption tool](../../agentevolver/tool/default/adoption.py), [extension manager](../../agentevolver/extension/server.py) |
| Execution records and training data | [Trace](../../agentevolver/trace/README.md), [visual views](../../agentevolver/visual/README.md), [Trajectory](../../agentevolver/trajectory/README.md) |

The evolution block describes the acting agent's self-evaluation, not a separate mandatory
evaluator service. Admission checks structure; functional evaluation uses real calls and
an exact candidate version. `record_use` is an additional receipt for tasks that explicitly
require verified improvement. A candidate can be provisionally available before the quality
judgment; registration alone is not adoption evidence. Model training and serving are not
represented as a completed in-system loop.
