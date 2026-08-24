---
name: kernel
description: "One Jupyter Server per project, and one kernel the agent, the Science REPL and JupyterLab all share. Keeps the execution history, so what ran is a fact rather than a mirror."
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

## One kernel, everything

The agent's `code_interpreter_tool`, the Science view's REPL and JupyterLab all
execute through the same Jupyter Server. That is why a variable the agent
defined is a variable you can print, and why the execution **history** below is
complete rather than a mirror somebody has to keep up to date.

The server is a subprocess of this container — the same one the agent runs in —
bound to loopback on an ephemeral port with no token, reachable only from here
and from the gateway's authorised proxy route.

It used to be a raw kernel here plus a second kernel in a "science" peer
container. That container:

- gave no isolation the agent did not already have — `bash_tool` runs here as
  root, on the same GPUs and disk; and
- held **different variables**, so the notebook you were reading and the agent
  that produced it disagreed about what existed.

## History

Every execution is recorded: the code, its outputs, the kernel's `[n]`, how long
it took, and whether it came from the agent or from you.

The Science view renders this list. It is not a document, so there is nothing to
save, reconcile or lose — `science.save` copies it out as an `.ipynb` when you
want a file.

## Rich output

Cells return a list of `KernelOutput`, each carrying its full MIME bundle.

This is the point. `matplotlib` returns a figure as a `display_data` message
carrying `image/png`; the previous pipeline kept only `text/plain`, which turned
every plot into the string `<Figure size 640x480 with 1 Axes>`. A notebook view
renders what `as_message()` can only name.

`as_message()` names rich outputs rather than inlining them — a base64 PNG helps
nobody reading a transcript, and the model only needs to know a figure exists.

## The workstation's furniture

| File | Responsibility |
|---|---|
| `notebooks.py` | The project's `.ipynb` files, and the kernel history written out as one |
| `compute.py` | GPUs, CPU, memory and disk — the host, which is what the kernel gets |

Both were `agentevolver/science/` until they moved here. That module held no
entity of its own: six of its eleven members forwarded straight to
`kernel_manager`, and the gateway had already stopped going through it for four
of the ten `science.*` routes — so the boundary was inconsistent in practice
before it was removed in principle.

Both are module-level functions rather than a class, for the reason the manager
criterion gives: the only state either holds is *one* cached GPU reading, and a
`self` that never distinguishes callers is a global with extra steps.

The Science *view* keeps its name and its `science.*` routes — the view is a
product surface. What went away is a module standing between it and the kernel.

`upstream()` lives on `kernel_manager` because it is `base_url` minus
`lab_path`, and those are the two things it owns. Handing the proxy the full
`base_url` doubles the prefix and every Lab request 404s, while both halves
still look right on their own.
