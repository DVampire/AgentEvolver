---
name: science
description: "A GPU-backed JupyterLab workstation, one container per project, working in the same workspace the agent works in. Human-facing: not an agent capability."
version: 1.0.0
type: module
category: interface
requirements: []
metadata:
  document_version: 1
---
# Science

A **workstation**: JupyterLab with the host's GPUs attached, a full scientific
stack, and LaTeX — for the open-ended half of the work. Train a model, run a
sweep, plot the result, write the paper.

Like the [canvas](../canvas/README.md) and the [IDE](../ide/README.md), this is
**human-facing**: the agent never calls into it, and it is not registered as a
capability the meta agent can see.

```python
from agentevolver.science import science_manager

instance = await science_manager.start(session_id, workspace_root=workspace)
instance.public()["path"]        # /science/<session>/lab — where the UI embeds it
await science_manager.compute(session_id)   # GPUs, CPU, memory, disk
```

## Why it is not the base environment

The agent and its tools — `bash_tool`, `code_interpreter_tool` — run in the
base container. They ship *with* the agent system, so there is nothing to route
anywhere, and base stays deliberately lean.

Science is the opposite kind of thing. It is expected to keep growing: today
PyTorch, transformers and TeX Live; tomorrow whatever a project needs. Putting
that in base would make every agent run carry the weight of a workstation
nobody asked for. So it is a **peer container**, one per project, exactly like
the Code view's VS Code container.

The image is still built `FROM agentevolver/base`, so the ~21GB of conda, CUDA
torch, kernels and the project itself are *shared layers already on the host* —
only the delta costs disk. It also means `import agentevolver` works in a
notebook, and the Lab's terminals open in the same Python the agent uses.

## Why it bypasses opensandbox

Every other sandbox goes through opensandbox. This one calls `docker run`
itself, because opensandbox's `[docker]` configuration has **no device
option** — a container it starts cannot be given GPUs at all, and a workstation
that cannot reach a GPU is not a workstation.

What opensandbox was doing for the others turns out to be small: a `docker run`
with an ephemeral loopback port, a `docker rm -f`, and an idle deadline this
manager enforces. See `agentevolver/sandbox/default/science.py`.

`SandboxConfig.gpus` exists for this and is read by nothing else — it is not
silently degraded elsewhere, because no opensandbox-backed sandbox looks at it.

## One per project, reaped on idle

There is no `session.close` in the gateway, so time is what frees these. A
workstation is started when the Science view first opens, kept alive by
heartbeats and by any proxied request, and reaped after two hours idle —
longer than the IDE's thirty minutes, because a training run can hold a
background tab for a while and killing the container kills the run with it.

At most two run at once. They are expensive; the least recently used is
evicted rather than starting a third.

## Notebooks are workspace files

A notebook is a real `.ipynb` under the project's workspace, not a private
format in a database. The same document therefore opens in the embedded
JupyterLab, in the Code view's editor, and in anything the user later runs over
the workspace — and `science_manager.notebooks()` can list them *before* the
container exists and *after* it has been reaped, because the notebook is a
workspace file and the container is not.

## Served on the UI's own origin

JupyterLab starts with `--ServerApp.base_url=/science/<session>/`, so every
absolute URL it emits already carries the prefix and the UI hosts the Lab at
`<whatever origin the browser used>/science/<session>/`.

This is the same fix the IDE needed. A per-session hostname is resolved by the
BROWSER, so it only ever worked when the browser ran on the server itself.
