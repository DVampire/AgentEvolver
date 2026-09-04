---
name: harbor
description: "Runs this framework's agents on Harbor-hosted benchmarks and lets Harbor score them: Harbor builds the task container and runs its own verifier, so the number stays comparable."
version: 1.0.0
type: module
category: benchmark
requirements: ["harbor"]
metadata: {}
---

# Harbor adapter

Runs this framework's agents on [Harbor](https://www.harborframework.com) benchmarks —
`deep-swe`, `terminal-bench`, and anything else published as a Harbor task set — and lets
Harbor score them.

## Why this direction

Harbor inverts the usual arrangement. The `Benchmark` classes next door hand tasks to a
launcher that owns the run; Harbor owns the run and calls an agent. It builds the task
container, passes an `instruction` and a `BaseEnvironment`, and afterwards executes the
task's own verifier inside that container to produce the reward.

Taking Harbor's side of that deal is what makes a score comparable. The alternative —
read Harbor's task directories, provision our own container, run our own copy of the
tests — scores a setup the leaderboard never ran. `deep-swe` 1.1 exists precisely because
grading inside the agent's own environment was not trustworthy enough, so this adapter
gives up controlling the environment in exchange for the number meaning something.

## Running

Harbor discovers an external agent by import path, so no fork of Harbor is involved:

```bash
pip install 'agentevolver[harbor]'

harbor run -d "deep-swe@1.1" \
    --agent agentevolver.benchmark.harbor:AgentEvolverAgent \
    --model llm_hub/claude-opus-5
```

Which agent and which tools come from this framework's own config, since that is what a
config file is for:

| Variable | Default | Meaning |
|---|---|---|
| `AGENTEVOLVER_CONFIG` | `configs/meta_agent.py` | Config the trial runs under |
| `AGENTEVOLVER_AGENT` | `meta_agent` | Agent to run |
| `AGENTEVOLVER_STEP_BUDGET` | `120` | Steps per task, which Harbor knows nothing about |
| `AGENTEVOLVER_EXTENSION_ROOT` | the config's | Writable tree for evolved components |

Harbor's `--model` beats the config's, because that name is part of what a leaderboard row
means. All of these are applied through the config's own `cfg_options` channel, so an
agent reads them when it is built rather than after.

Set `AGENTEVOLVER_EXTENSION_ROOT` when the repository's `extension/` is not writable — a
shared checkout can have it owned by another account, and then every trial fails in setup
on a manifest it cannot open, long before the task is read.

## How it fits

Every tool in this repository talks to `Sandbox` and nothing below it, so
`HarborSandbox` — Harbor's environment wearing that interface — is what lets `bash_tool`,
`apply_patch_tool` and the rest run inside a Harbor task without one of them changing.
Both sides are async and the operations line up (`exec` → `run_command`, native file
transfer replacing the base class's base64-through-a-shell), so it is a translation and
not a bridge.

The sandbox has no lifecycle: Harbor starts the container before calling the agent and
stops it after the verifier has run, so `start` and `destroy` are deliberately no-ops.
Tearing the container down when the agent finishes would delete the filesystem the reward
is computed from.

## The caveat that travels with every number

A leaderboard entry is a specific agent harness on a specific model. `deep-swe` publishes
its numbers using `mini-swe-agent`; swapping the harness changes what is being measured
even when the model is identical. A score from here is this framework's score on that task
set — not a replication of the published row.
