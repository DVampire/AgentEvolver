"""Run this framework's agents on Harbor benchmarks, scored by Harbor.

Harbor inverts the usual direction. Our own `Benchmark` classes hand tasks to a launcher
that owns the run; Harbor owns the run and calls an agent — it builds the task container,
passes an ``instruction`` and a ``BaseEnvironment``, and afterwards executes the task's own
verifier inside that container to produce the reward.

Taking Harbor's side of that deal is what makes a score comparable. The alternative — read
Harbor's task directories, provision our own container, run our own copy of the tests —
scores a setup the leaderboard never ran, and `deep-swe` 1.1 exists precisely because
grading in the agent's own environment was not trustworthy enough. So this adapter gives
up controlling the environment in exchange for the number meaning something.

Harbor discovers an external agent by import path, so nothing here requires a fork:

    harbor run -d "deep-swe@1.1" \\
        --agent agentevolver.benchmark.harbor:AgentEvolverAgent \\
        --model llm_hub/claude-opus-5

One caveat travels with every number this produces: a leaderboard entry is a specific
agent harness on a specific model, and swapping the harness changes what is being measured
even when the model is the same. A score from here is this framework's score on that task
set, not a replication of the published row.
"""

from .agent import AgentEvolverAgent

__all__ = ["AgentEvolverAgent"]
