"""Public benchmark facade. Concrete implementations are registered internally."""
from .types import Benchmark, BenchmarkInfo, BenchmarkTaskContext, BenchmarkConfig, Task, Stats, EvaluationResult
from .server import benchmark_manager, BenchmarkManager
from . import default as _default  # Register built-ins without exporting implementations.

__all__ = [
    "Benchmark", "BenchmarkInfo", "BenchmarkTaskContext", "BenchmarkConfig", "Task", "Stats", "EvaluationResult",
    "benchmark_manager", "BenchmarkManager",
]
