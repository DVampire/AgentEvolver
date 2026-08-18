"""Benchmark Manager implementation"""
import os
from typing import Any, Dict, List, Optional, Union, Type
from pydantic import BaseModel, ConfigDict, Field

from agentevolver.paths import P, path_manager
from agentevolver.config import config
from agentevolver.utils import assemble_workspace_path
from agentevolver.logger import logger
from agentevolver.benchmark.types import BenchmarkConfig, Benchmark, Task, Stats
from agentevolver.benchmark.context import BenchmarkContextManager

class BenchmarkManager(BaseModel):
    """Benchmark Manager for managing benchmark registration and lifecycle"""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    
    base_dir: str = Field(default=None, description="Base directory for benchmarks")
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Created lazily: config may not be loaded at import time. initialize()
        # reconfigures it with the proper base_dir.
        self.benchmark_context_manager: Optional[BenchmarkContextManager] = None
        self._registered_benchmarks: Dict[str, BenchmarkConfig] = {}

    def _ensure_context_manager(self) -> BenchmarkContextManager:
        """Lazily create the context manager so methods work before initialize().

        Every other manager has this. Without it, asking this one anything before
        `initialize()` raised `AttributeError` — which reads as a missing feature
        rather than as "no benchmarks are loaded yet". The canvas palette asks, and
        catches the failure per registry, so the visible symptom was every benchmark
        node quietly absent from the palette rather than an error anybody saw.
        """
        if self.benchmark_context_manager is None:
            self.benchmark_context_manager = BenchmarkContextManager()
        return self.benchmark_context_manager

    async def initialize(self, benchmark_names: Optional[List[str]] = None):
        """Initialize benchmarks."""
        self.base_dir = assemble_workspace_path(path_manager.under(config.log_root, P.LOG_MODULE, module="benchmark"))
        
        self.benchmark_context_manager = BenchmarkContextManager(
            base_dir=self.base_dir,
        )
        await self.benchmark_context_manager.initialize(benchmark_names=benchmark_names)
        logger.info("| ✅ Benchmark systems initialization completed")

    async def register(self, 
                       benchmark: Union[Benchmark, Type[Benchmark]], 
                       *, 
                       override: bool = False, 
                       **kwargs: Any) -> BenchmarkConfig:
        """Register a benchmark system asynchronously."""
        benchmark_config = await self._ensure_context_manager().register(benchmark, override=override, **kwargs)
        self._registered_benchmarks[benchmark_config.name] = benchmark_config
        return benchmark_config

    async def update(self, 
                     benchmark_name: str, 
                     benchmark: Union[Benchmark, Type[Benchmark]], 
                     new_version: Optional[str] = None, 
                     description: Optional[str] = None,
                     **kwargs: Any) -> BenchmarkConfig:
        """Update an existing benchmark system."""
        benchmark_config = await self._ensure_context_manager().update(benchmark_name, benchmark, new_version, description, **kwargs)
        self._registered_benchmarks[benchmark_config.name] = benchmark_config
        return benchmark_config

    async def get_info(self, benchmark_name: str) -> Optional[BenchmarkConfig]:
        """Get benchmark configuration by name."""
        return self._ensure_context_manager()._benchmark_configs.get(benchmark_name)

    async def list(self) -> List[str]:
        """List all registered benchmarks."""
        return list(self._ensure_context_manager()._benchmark_configs.keys())

    async def get(self, name: str) -> Optional[Benchmark]:
        """Get benchmark instance by name."""
        return await self._ensure_context_manager().get(name)

    async def reset(self, name: str, split: Optional[str] = None) -> Optional[Task]:
        """Reset benchmark progress."""
        return await self._ensure_context_manager().reset(name, split)

    async def step(self, name: str) -> Optional[Task]:
        """Get next benchmark task."""
        return await self._ensure_context_manager().step(name)

    async def eval(self, name: str, task: Task) -> Optional[Task]:
        """Evaluate a benchmark task."""
        return await self._ensure_context_manager().eval(name, task)

    async def stats(self, name: str) -> Optional[Stats]:
        """Get benchmark statistics."""
        return await self._ensure_context_manager().stats(name)

    async def restore(self, name: str, version: str) -> Optional[BenchmarkConfig]:
        """Restore a specific version of a benchmark."""
        benchmark_config = await self._ensure_context_manager().restore(name, version)
        if benchmark_config:
            self._registered_benchmarks[name] = benchmark_config
        return benchmark_config

    async def __call__(self, benchmark_name: str, results: List[Dict[str, str]], concurrency: int = 10) -> float:
        """Batch evaluation entry point"""
        benchmark = await self.get(benchmark_name)
        if not benchmark:
            raise RuntimeError(f"Benchmark {benchmark_name} not initialized.")
            
        import asyncio
        sem = asyncio.Semaphore(concurrency)

        async def _safe_eval(res):
            async with sem:
                t_id = str(res.get("task_id", ""))
                pred = res.get("prediction", "")
                gt = res.get("ground_truth")
                
                if not t_id:
                    return 0.0
                
                try:
                    # Create a Task object for evaluation
                    task = Task(
                        task_id=t_id,
                        result=pred,
                        ground_truth=gt
                    )
                    evaluated_task = await benchmark.eval(task)
                    score = evaluated_task.score if evaluated_task else 0.0
                    _record_reward(t_id, res, score, evaluator=benchmark_name)
                    return score
                except Exception as e:
                    logger.error(f"| ❌ Eval failed for task {t_id}: {e}")
                    return 0.0

        tasks = [_safe_eval(res) for res in results]
        logger.info(f"| 🚀 Starting batch evaluation for {len(tasks)} items in benchmark '{benchmark_name}'")
        scores = await asyncio.gather(*tasks)
        
        avg_score = sum(scores) / len(scores) if scores else 0.0
        logger.info(f"| ✅ Batch evaluation for '{benchmark_name}' completed. Avg score: {avg_score:.4f}")
        return avg_score

    async def cleanup(self):
        """Cleanup all benchmarks using context manager."""
        if hasattr(self, 'benchmark_context_manager'):
            await self._ensure_context_manager().cleanup()

# Global instance
benchmark_manager = BenchmarkManager()


def _record_reward(
    task_id: str,
    result: dict,
    score: float,
    *,
    evaluator: str = "benchmark",
) -> None:
    """Backfill the score onto the run that earned it.

    The score was computed per task and then averaged away, so every trajectory this
    framework wrote carried `reward = 0.0` — 61 of 61 in a real output tree, success and
    failure alike. A trajectory exists to be trained on and the reward *is* the training
    signal, so the whole corpus said "this run was worth nothing" about runs that had
    just been scored.

    Both sides were already built for this: `set_reward` keys on the run's `task_id`,
    which `Response.data` carries, and `set_reward_by_session` exists — its docstring says
    so — for the benchmark case of one session per task. Nothing joined them.

    Never fatal. A benchmark's number is the thing being asked for; failing it because
    the trajectory could not be annotated would throw away the answer to keep the
    footnote.
    """
    from agentevolver.trajectory import trajectory_manager

    try:
        if trajectory_manager.set_reward(
            task_id, score, evaluator=evaluator,
        ) is not None:
            return
    except Exception as error:                                      # noqa: BLE001
        logger.warning(f"| ⚠️ could not record reward for task {task_id}: {error}")
        return

    # A benchmark that ran one agent session per task holds the session id, not the run's
    # task_id; `set_reward` above will have found nothing and said so.
    session_id = str(result.get("session_id") or "")
    if session_id:
        try:
            trajectory_manager.set_reward_by_session(
                session_id, score, evaluator=evaluator,
            )
        except Exception as error:                                  # noqa: BLE001
            logger.warning(f"| ⚠️ could not record reward for session {session_id}: {error}")
