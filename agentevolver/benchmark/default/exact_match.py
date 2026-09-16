"""A dataset-free evaluator: score predictions against ground truth.

Unlike the dataset-bound benchmarks (gsm8k, aime, …) that download their own
data, ``exact_match`` scores whatever ``{prediction, ground_truth}`` pairs are
handed to it — so it is the general evaluator a canvas ``benchmark`` node uses
to close a data pipeline (``… -> to_eval_records -> benchmark``). It has no
dataset, so ``initialize()`` is a no-op and startup stays cheap.
"""

from typing import List, Optional

from pydantic import PrivateAttr

from agentevolver.registry import BENCHMARK
from agentevolver.benchmark.types import Benchmark, Stats, Task


@BENCHMARK.register_module(force=True)
class ExactMatchBenchmark(Benchmark):
    """Score predictions against ground truth (numeric-tolerant exact match)."""

    name: str = "exact_match"
    description: str = "Evaluate {prediction, ground_truth} pairs by numeric/exact match."


    async def _initialize(self):
        """No dataset to load."""
        self._tasks = []


    async def _step(self) -> Optional[Task]:
        return None

    async def _eval(self, task: Task) -> Task:
        """1.0 when the prediction equals the ground truth (numeric or string)."""
        if self.model_name:
            task.score = await self.llm_judge(task)
            return task
        pred = str(task.result).strip() if task.result is not None else ""
        gt = str(task.ground_truth).strip() if task.ground_truth is not None else ""
        score = 0.0
        if pred:
            try:
                score = 1.0 if abs(float(pred) - float(gt)) < 1e-9 else 0.0
            except (TypeError, ValueError):
                score = 1.0 if pred == gt else 0.0
        task.score = score
        return task

    async def _stats(self) -> Stats:
        return Stats(total=len(self._tasks))
