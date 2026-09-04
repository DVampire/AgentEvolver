"""SWE-bench Verified and SWE-bench Pro — issue resolution scored by hidden tests.

Both were reachable only through their launchers, which each called
``load_dataset("<org>/<name>", split="test")`` directly. That put the rows in the
HuggingFace cache rather than under ``datasets/``, where every other benchmark in this
repository keeps its data and where `ensure_dataset` puts it — so the two heaviest
datasets in the project were the two nobody could see on disk, and neither appeared in
the benchmark registry that lists what this framework can run.

Loading is what these classes own. Running is not: an instance is graded inside its own
Docker image against a hidden suite, with the answer key deliberately outside the agent's
container, and the launchers in ``examples/`` own that orchestration. Putting the loader
here gives both halves one definition of what an instance is and which of its fields an
agent may see — the distinction the whole GT-safety design rests on, and one that was
previously written out twice.
"""

import os
from typing import Dict, List, Optional, Tuple

from pydantic import ConfigDict, Field, PrivateAttr

from agentevolver.benchmark.types import Benchmark, Task
from agentevolver.logger import logger
from agentevolver.registry import BENCHMARK
from agentevolver.utils import dedent

SYSTEM_PROMPT = dedent("""
    You are an expert software engineer working inside a real repository at a known
    commit. You are given one issue report and the repository it belongs to.

    Write the source change that resolves the issue. A hidden test suite decides the
    outcome: some tests currently fail and must pass after your change, and the tests that
    already pass must keep passing. You cannot see either list.

    Change only what the issue calls for. A patch that resolves the issue and breaks
    something else scores nothing.

    Task:
""")


class SWEBenchBenchmark(Benchmark):
    """Shared loader for the SWE-bench family: rows in, GT-safe tasks out."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    #: Fields an agent's container may see. Everything else in a row is the answer key
    #: (`patch`, `test_patch`, `fail_to_pass`, `pass_to_pass`) or host-side scoring
    #: metadata. Subclasses set this; the base deliberately has no default, so a new
    #: variant cannot inherit another benchmark's idea of what is safe to reveal.
    safe_fields: Tuple[str, ...] = Field(default=())

    system_prompt: Optional[str] = Field(default=SYSTEM_PROMPT)

    _data_records: List[Dict] = PrivateAttr(default_factory=list)
    _index: int = PrivateAttr(default=0)

    def __init__(self, base_dir: Optional[str] = None, start: Optional[int] = None,
                 end: Optional[int] = None, **kwargs):
        super().__init__(base_dir=base_dir, start=start, end=end, **kwargs)

    async def initialize(self):
        # Built on first use rather than at construction: the registry instantiates every
        # benchmark at startup, before a session is bound, so doing this in __init__
        # scaffolded empty directories under the unbound root.
        from agentevolver.benchmark.utils import ensure_dataset
        from datasets import load_dataset

        os.makedirs(self.base_dir, exist_ok=True)
        local = ensure_dataset(os.path.basename(self.path), self.hf_repo_id)
        dataset = load_dataset(local, split=self.split)
        self._data_records = self._apply_slice(list(dataset))
        logger.info(f"| 📚 {self.name}: {len(self._data_records)} instance(s) from {local}")
        await self.reset()

    def instances(self) -> List[Dict]:
        """Every loaded row, oracle fields included — for the host-side grader only.

        The launcher needs `patch`, `test_patch` and the two test lists to grade, and it
        runs on the host where the answer key already lives. Nothing that reaches a
        container may come from here; use `safe_instance` for that.
        """
        return list(self._data_records)

    def safe_instance(self, row: Dict) -> Dict:
        """The subset of a row a container may see — never the gold patch or the tests."""
        return {key: row.get(key, "") for key in self.safe_fields}

    async def reset(self) -> Optional[Task]:
        self._index = 0
        return await self.step()

    async def step(self) -> Optional[Task]:
        if self._index >= len(self._data_records):
            return None
        record = self._data_records[self._index]
        self._index += 1
        return Task(
            task_id=str(record.get("instance_id", f"{self._index:04d}")),
            input=str(record.get("problem_statement", "")),
            system_prompt=self.system_prompt,
            # The resolution oracle is the hidden suite, which only the host-side grader
            # may consult. Carrying it on the Task would put it one attribute access away
            # from whatever renders a prompt.
            ground_truth=None,
            extra=self.safe_instance(record),
        )

    async def eval(self, task: Task) -> Optional[Task]:
        """Scoring belongs to the official harness, which runs on the host.

        An answer here would have to come from something this class can see, and what it
        can see is exactly what the agent saw. The launcher writes the real score back
        onto the task after the grader has run.
        """
        if task.score is None:
            task.score = 0.0
        return task


@BENCHMARK.register_module(force=True)
class SWEBenchVerifiedBenchmark(SWEBenchBenchmark):
    """SWE-bench Verified — 500 human-validated Python issues from 12 repositories."""

    name: str = Field(default="swebench_verified")
    path: str = Field(default="datasets/SWE-bench_Verified")
    hf_repo_id: str = Field(default="SWE-bench/SWE-bench_Verified")
    # Verified carries no requirements/interface: the issue text is the whole spec.
    safe_fields: Tuple[str, ...] = Field(
        default=("instance_id", "repo", "base_commit", "problem_statement")
    )


@BENCHMARK.register_module(force=True)
class SWEBenchProBenchmark(SWEBenchBenchmark):
    """SWE-bench Pro — 731 issues across Python, Go, JS and TS, with longer patches."""

    name: str = Field(default="swebench_pro")
    path: str = Field(default="datasets/SWE-bench_Pro")
    hf_repo_id: str = Field(default="ScaleAI/SWE-bench_Pro")
    # Pro adds a written requirements list and an interface spec to the issue text.
    safe_fields: Tuple[str, ...] = Field(
        default=("instance_id", "repo", "repo_language", "base_commit",
                 "problem_statement", "requirements", "interface")
    )
