"""SWE-bench Pro eval tool — the agent asks the real hidden suite to score its current patch.

This is the *in-sandbox* half of a file bridge, identical in shape to programbench_eval.py.
The hidden test suite (the instance's `test_patch` plus its `fail_to_pass` / `pass_to_pass`
lists) is deliberately ABSENT from the task container — that absence is the benchmark's
answer key, and keeping it out of the agent's environment is the whole GT-safety design. So
the tool does not run the tests itself: it commits the agent's current work, writes a request
onto a bind-mounted bridge directory, and blocks until the host-side watcher
(``examples/run_swebench_pro.py``) computes the patch from the committed workspace, runs the
official ``swe_bench_pro_eval.py`` on the host for this one instance, and writes back a
report.

What comes back is deliberately narrow, and that narrowness is the point:
**COUNTS ONLY** — how many hidden `fail_to_pass` tests the patch now makes pass, how many
`pass_to_pass` tests still pass, and whether the instance is fully resolved. The failing
test NAMES are withheld too: unlike ProgramBench (where a failing name is a harmless pointer
you still have to reproduce from a reference binary), for SWE-bench Pro the `fail_to_pass`
names ARE the resolution oracle, so returning them would hand the agent the exact test
points. The host computes the counts on its side (where the oracle lives) and returns only
the numbers — a progress signal, never the answer key.

Using the grader as an oracle at all means iterating against a signal a single-shot run does
not have, so a score from a run that called this tool is **not directly comparable to the
public leaderboard**. The launcher records how many times it was called so that caveat
travels with the number.

Outside a SWE-bench Pro launcher (no bridge in the environment) the tool reports that it is
unavailable and changes nothing.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from pydantic import Field

from agentevolver.registry import TOOL
from agentevolver.response.types import Response
from agentevolver.tool.types import Tool

from .bridge import DEFAULT_BUDGET, format_swebench_counts, request_evaluation

#: Container path of the bind-mounted bridge, and the per-run call budget. Both are set by
#: the launcher when it acquires the sandbox; their absence is exactly how the tool knows it
#: is running outside a launcher and should no-op. Shared with programbench_eval — only one
#: benchmark launcher runs at a time.
_WORKSPACE = "/workspace"
_TASK_WORKSPACE_ENV = "AGENTEVOLVER_TASK_WORKSPACE"
_DEFAULT_BUDGET = DEFAULT_BUDGET

_DESCRIPTION = (
    "Run the hidden test suite on your CURRENT patch and report progress as COUNTS ONLY "
    "(how many fail_to_pass now pass, how many pass_to_pass still pass, whether resolved) — "
    "no test names, bodies, or expected outputs. A heavy, rate-limited operation."
)

_GUIDANCE = """
Ask the real grader to run the instance's hidden tests against your CURRENT edits and return aggregate progress counts. This is the one signal that reaches past your own reasoning to the actual scoring suite.

**What you get back**: COUNTS ONLY — how many of the hidden `fail_to_pass` tests your patch now makes pass (`resolved X/N`), how many previously-passing `pass_to_pass` tests still pass (`preserved Y/M`), and whether the instance is fully resolved. You do NOT get the failing test names, the test bodies, the expected outputs, the `test_patch`, or the reference fix — those are the answer key and are withheld. This is a progress signal, not a to-do list: it tells you whether your last batch of changes moved the score and whether it broke anything, so you can reason about your approach — never a pointer to the exact behaviour under test.

**It is heavy and rate-limited**. Each call runs the selected hidden tests on the host — minutes per call — and you get only a handful of calls per run (the budget is stated when you exceed it). So:
- Do NOT call it after a trivial edit. Make a batch of real changes, verify what you can with the repo's OWN existing tests (run them yourself with the shell — that is free), then spend one grader eval to see which fail_to_pass are still open.
- The tool commits your workspace before scoring, so make sure the repo is in a runnable state (no syntax errors) before calling — an eval on a broken checkout just spends a call to tell you it does not run.

**It blocks** until the host finishes scoring. Use the returned counts only as a progress
signal; no failing names are exposed.
"""

_EXAMPLES = [
    '{"name": "swebench_pro_eval_tool", "args": {"focus": "handled the null-uid branch in loadUserInfo per the issue"}}',
]


@TOOL.register_module(force=True)
class SWEBenchProEvalTool(Tool):
    """Bridge to the host-side SWE-bench Pro grader and return counts only."""

    name: str = "swebench_pro_eval_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = "workspace_write"
    mutates: bool = True

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, focus: str = "", **kwargs) -> Response:
        """Score the current patch with the real grader and return counts only.

        Args:
            focus: One line — what you just changed / want to check. Recorded for the run's
                trace; does not affect scoring.
        """
        return await request_evaluation(
            tool_name=self.name,
            benchmark_label="SWE-bench Pro",
            workspace=os.environ.get(_TASK_WORKSPACE_ENV, _WORKSPACE),
            focus=focus,
            format_result=_format_result,
            exhausted_guidance=(
                "keep improving and run the repository's own tests yourself (free)."
            ),
            timeout_guidance="The host may still be scoring; try again later or keep working.",
        )


_format_result = format_swebench_counts
