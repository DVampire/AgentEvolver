# Task documents

Every HTML task in this directory uses the shared task renderer. Keep the task's
objectives, requirements and acceptance criteria here; execution policies and methods
belong in configuration, agent prompts and skills.

Use one `<div class="task">` as the body wrapper. Its direct children use these
section tags consistently:

| Tag | Content |
| --- | --- |
| `objective` | Purpose, audience and intended outcome |
| `requirements` | Scope, behavior, content and quality requirements |
| `interface` | Interfaces, inputs, outputs and interaction contracts |
| `acceptance` | Observable criteria and verification evidence |
| `plan` | Requested milestones and delivery stages |
| `deliverables` | Artifacts and handoff requirements |
| `constraints` | Boundaries, exclusions and limitations |
| `notes` | Context, references and supporting materials |

Only include relevant sections. Tags may repeat for long documents; identify chapters
with headings and stable `id` attributes instead of inventing new section tags.

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Project brief</title>
  <link rel="stylesheet" href="../../agentevolver/visual/task/style.css">
  <script src="../../agentevolver/visual/task/app.js" defer></script>
</head>
<body>
<div class="task">
<objective>
Build a useful experience for the intended audience.
</objective>
<requirements data-format="html" id="reader-journey">
  <h2>Reader journey</h2>
  <p>Describe the behavior visitors should be able to observe.</p>
  <ul><li>Preserve their work when they return.</li></ul>
</requirements>
<acceptance>
- Demonstrate the complete journey with actual results.
</acceptance>
</div>
</body>
</html>
```

The example paths above apply to HTML directly under `examples/tasks`. A document at
`examples/tasks/<family>/<project>/task.html` needs `../../../../agentevolver/visual/task/`
instead. Resolve both paths from the document's own directory. Do not copy styles or
scripts into task folders, embed them inline, or introduce a separate theme.

Markdown is the default section content. Escape literal angle brackets in code examples
as `&lt;` and `&gt;`, including inside backticks and fenced code blocks. For authored HTML,
set `data-format="html"` on the section; the renderer preserves headings, nested lists,
links, code and tables instead of converting their text back to Markdown. Both content
forms share the same section labels, visual styles and keyboard-accessible collapse
controls. The task loader extracts the body and section labels without running scripts;
browser controls and visual resources never enter the task prompt.
