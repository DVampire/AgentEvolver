---
name: kernel
description: "One live interpreter per project, held open so state persists across calls. Runs in the base environment beside the workspace, and carries rich output — figures, tables, HTML — back instead of flattening it to text."
version: 1.0.0
type: module
category: infrastructure
requirements: [jupyter_client, ipykernel]
metadata: {}
---
# Kernel

A **kernel** is a live interpreter that outlives the call that used it, so a
later call sees the variables an earlier one defined.

```python
from agentevolver.kernel import kernel_manager

await kernel_manager.execute("x = 41", key=project_id)
result = await kernel_manager.execute("print(x + 1)", key=project_id)   # 42
```

## One per project

The kernel is a **resource**, like the project's files: one, shared by every
conversation and every view in it.

That is deliberate. `ctx.id` — the scope of memory, budgets and todos — is a
*conversation* id, so keying the interpreter off it would hand every new line
of dialogue a blank interpreter, and every workflow run another one. Resources
key off the project; state keys off `ctx.id`. They are not the same axis.

## It runs here, not in a container

The agent system and its tools ship together, so `code_interpreter` runs in the
base environment like every other tool. It used to start a peer container of
its own, which:

- bought no isolation the agent did not already have — `bash_tool` runs here
  too, so anything the interpreter could do was already reachable; and
- **mounted nothing**, so code could not read the files the agent had just
  written. A `pd.read_csv("data.csv")` after a `bash` that wrote `data.csv`
  raised `FileNotFoundError`, because the two ran in different filesystems.

The kernel now starts in `config.workspace_root`, so relative paths mean the
same thing to it, to bash, and to the files pane.

## Rich output

Cells return a list of `KernelOutput`, each carrying its full MIME bundle.

This is the point. `matplotlib` returns a figure as a `display_data` message
carrying `image/png`; the previous pipeline kept only `text/plain`, which turned
every plot into the string `<Figure size 640x480 with 1 Axes>`. A notebook view
renders what `as_message()` can only name.

`as_message()` names rich outputs rather than inlining them — a base64 PNG helps
nobody reading a transcript, and the model only needs to know a figure exists.
