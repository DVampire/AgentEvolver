import os
from typing import Optional, List, Dict

from pydantic import Field, ConfigDict, PrivateAttr

from agentevolver.benchmark.types import Benchmark, Task, Stats
from agentevolver.registry import BENCHMARK
from agentevolver.utils import dedent, is_same

SYSTEM_PROMPT = dedent("""
    You are a deep-research assistant. You will be given a research question that
    asks you to analyze a set of entities across several research dimensions.
    Conduct a systematic analysis and produce a clear, well-organized answer that
    covers every entity and every requested dimension. Cite sources where possible
    and clearly mark estimates or unknown values.

    Please answer the following research question:
""")


@BENCHMARK.register_module(force=True)
class DeepWebBenchmark(Benchmark):
    """
    DEEPWEB-BENCH – a collection of 100 English deep-research benchmark cases.
    Each case asks a model to analyze 6-10 entities across 6-10 research dimensions.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = Field(default="deepweb", description="The name of the benchmark")
    path: str = Field(default="datasets/deepweb-bench", description="The path to the benchmark dataset")
    hf_repo_id: str = Field(default="deepweb-bench-anon/deepweb-bench", description="HuggingFace repo to download the dataset from when it is missing locally.")

    _data_records: List[Dict] = PrivateAttr(default_factory=list)

    system_prompt: Optional[str] = Field(default=SYSTEM_PROMPT, description="The system prompt for the benchmark")

    def __init__(self, base_dir: Optional[str] = None, start: Optional[int] = None, end: Optional[int] = None, **kwargs):
        super().__init__(base_dir=base_dir, start=start, end=end, **kwargs)

    async def _initialize(self):
        # Created on first use, not at construction: the registry builds every
        # benchmark at startup, before any session is bound, so doing it in
        # __init__ scaffolded empty directories under the unbound root.
        os.makedirs(self.base_dir, exist_ok=True)
        from agentevolver.benchmark.utils import ensure_dataset
        from agentevolver.data.deepweb import DeepWebDataset
        local_dir = ensure_dataset(os.path.basename(self.path), self.hf_repo_id)
        dataset = DeepWebDataset(path=local_dir)
        self._data_records = self._apply_slice(dataset.data)


    async def _step(self) -> Optional[Task]:
        if self._index >= len(self._data_records):
            return None

        record = self._data_records[self._index]
        self._index += 1

        question = record.get("question_md", "")
        ground_truth = record.get("reference_answer_md", "")
        task_id = record.get("case_id") or f"{self._index:04d}"

        extra = {
            k: v for k, v in record.items()
            if k not in ["question_md", "reference_answer_md", "case_id"]
        }

        return Task(
            task_id=task_id,
            input=question,
            system_prompt=self.system_prompt,
            ground_truth=ground_truth,
            extra=extra,
        )

    async def _eval(self, task: Task) -> Task:
        result = str(task.result).strip() if task.result is not None else ""
        ground_truth = str(task.ground_truth).strip() if task.ground_truth is not None else ""

        task.result = result
        task.ground_truth = ground_truth

        if self.model_name:
            task.score = await self.llm_judge(task)
            return task

        # Deep-research answers are long-form; exact match is only a coarse fallback.
        task.score = 1.0 if result and is_same(result, ground_truth) else 0.0
        return task

    async def _stats(self) -> Stats:
        return Stats(total=len(self._data_records))
