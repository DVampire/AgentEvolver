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

import json
import os
import subprocess
import time
from typing import Any, Dict, List

from pydantic import Field

from agentevolver.tool.types import Tool
from agentevolver.response.types import Response, ResponseType
from agentevolver.logger import logger
from agentevolver.registry import TOOL

#: Container path of the bind-mounted bridge, and the per-run call budget. Both are set by
#: the launcher when it acquires the sandbox; their absence is exactly how the tool knows it
#: is running outside a launcher and should no-op. Shared with programbench_eval — only one
#: benchmark launcher runs at a time.
_BRIDGE_ENV = "AGENTEVOLVER_EVAL_BRIDGE"
_BUDGET_ENV = "AGENTEVOLVER_EVAL_BUDGET"
_WORKSPACE = "/workspace"
#: A full grade is a Docker build plus the selected hidden tests — minutes, not seconds.
_POLL_TIMEOUT_SECONDS = 1500
_POLL_INTERVAL_SECONDS = 3.0
_DEFAULT_BUDGET = 4

_DESCRIPTION = (
    "Run the hidden test suite on your CURRENT patch and report progress as COUNTS ONLY "
    "(how many fail_to_pass now pass, how many pass_to_pass still pass, whether resolved) — "
    "no test names, bodies, or expected outputs. A heavy, rate-limited operation."
)

_GUIDANCE = """
Ask the real grader to run the instance's hidden tests against your CURRENT edits and return which `fail_to_pass` tests still fail. This is the one signal that reaches past your own reasoning to the actual scoring suite.

**What you get back**: COUNTS ONLY — how many of the hidden `fail_to_pass` tests your patch now makes pass (`resolved X/N`), how many previously-passing `pass_to_pass` tests still pass (`preserved Y/M`), and whether the instance is fully resolved. You do NOT get the failing test names, the test bodies, the expected outputs, the `test_patch`, or the reference fix — those are the answer key and are withheld. This is a progress signal, not a to-do list: it tells you whether your last batch of changes moved the score and whether it broke anything, so you can reason about your approach — never a pointer to the exact behaviour under test.

**It is heavy and rate-limited**. Each call runs the selected hidden tests on the host — minutes per call — and you get only a handful of calls per run (the budget is stated when you exceed it). So:
- Do NOT call it after a trivial edit. Make a batch of real changes, verify what you can with the repo's OWN existing tests (run them yourself with the shell — that is free), then spend one grader eval to see which fail_to_pass are still open.
- The tool commits your workspace before scoring, so make sure the repo is in a runnable state (no syntax errors) before calling — an eval on a broken checkout just spends a call to tell you it does not run.

**It blocks** until the host finishes scoring. Treat the returned failing names as a prioritised to-do list, not as answers.
"""

_EXAMPLES = [
    '{"name": "swebench_pro_eval_tool", "args": {"focus": "handled the null-uid branch in loadUserInfo per the issue"}}',
]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", _WORKSPACE, *args], capture_output=True, text=True, timeout=120
    )


@TOOL.register_module(force=True)
class SWEBenchProEvalTool(Tool):
    """Bridge the agent's request to the host-side SWE-bench Pro grader and return failing test names."""

    name: str = "swebench_pro_eval_tool"
    description: str = _DESCRIPTION
    guidance: str = _GUIDANCE
    examples: List[str] = _EXAMPLES
    metadata: Dict[str, Any] = Field(default={}, description="The metadata of the tool")
    enable_evolving: bool = Field(default=False, description="Whether the tool may be evolved (self-optimized)")
    permission_mode: str = Field(
        default="read_only",
        description="Reads a score; the only write is a bookkeeping checkpoint commit in the agent's own workspace.",
    )

    def __init__(self, enable_evolving: bool = False, **kwargs):
        super().__init__(enable_evolving=enable_evolving, **kwargs)

    async def __call__(self, focus: str = "", **kwargs) -> Response:
        """Score the current patch with the real grader and return the failing test names.

        Args:
            focus: One line — what you just changed / want to check. Recorded for the run's
                trace; does not affect scoring.
        """
        bridge = os.environ.get(_BRIDGE_ENV)
        if not bridge:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=("swebench_pro_eval_tool is only available inside a SWE-bench Pro launcher "
                         "(no eval bridge in this environment); nothing was evaluated."),
            )
        try:
            budget = int(os.environ.get(_BUDGET_ENV) or _DEFAULT_BUDGET)
        except ValueError:
            budget = _DEFAULT_BUDGET

        try:
            os.makedirs(bridge, exist_ok=True)
            seq = len([n for n in os.listdir(bridge) if n.startswith("request-") and n.endswith(".json")])
            if seq >= budget:
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=(f"Grader eval budget exhausted ({seq}/{budget} used). No more hidden-suite "
                             f"evals this run — keep improving, and run the repo's own existing tests "
                             f"yourself (free) to check your work."),
                )

            # Commit so the host diffs exactly what exists now. The watcher computes the patch
            # as `git diff` from the base commit over the committed tree. Empty commits are
            # fine — bookkeeping. Skipped when the workspace is not a git repo.
            if os.path.isdir(os.path.join(_WORKSPACE, ".git")):
                _git("add", "-A")
                _git("-c", "user.email=agent@local", "-c", "user.name=agent",
                     "commit", "-m", f"[eval-checkpoint {seq}] {focus}".strip(), "--allow-empty", "-q")

            request_path = os.path.join(bridge, f"request-{seq}.json")
            response_path = os.path.join(bridge, f"response-{seq}.json")
            tmp = request_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"seq": seq, "ts": time.time(), "focus": focus}, handle)
            os.replace(tmp, request_path)
            logger.info(f"| 🧪 swebench_pro_eval_tool: requested grader eval #{seq} (focus={focus!r})")

            deadline = time.time() + _POLL_TIMEOUT_SECONDS
            while time.time() < deadline:
                if os.path.isfile(response_path):
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            else:
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=(f"Grader eval #{seq} did not return within {_POLL_TIMEOUT_SECONDS}s. "
                             f"The host may still be scoring; try again later or keep working."),
                )

            with open(response_path, encoding="utf-8") as handle:
                result = json.load(handle)
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ swebench_pro_eval_tool failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Eval request failed: {e}")

        return Response(type=ResponseType.TOOL, success=True, message=_format_result(seq, budget, result))


def _format_result(seq: int, budget: int, result: Dict[str, Any]) -> str:
    """Turn the host's narrow report into a progress signal. COUNTS ONLY — never test names,
    bodies, or expected outputs (for SWE-bench Pro the fail_to_pass names ARE the resolution
    oracle, so they are withheld; the agent gets only how many pass)."""
    error_code = result.get("error_code")
    header = f"Grader eval #{seq} (of {budget} allowed this run)"

    if error_code:
        detail = result.get("error_details") or ""
        return (f"{header}: the grader could not score this patch (error_code={error_code}). "
                f"{detail}\nMost often this means the patch does not apply cleanly or the repo does "
                f"not run. Fix it, verify the repo runs, then re-evaluate.").strip()

    f2p_pass = result.get("fail_to_pass_resolved")
    f2p_total = result.get("fail_to_pass_total")
    p2p_pass = result.get("pass_to_pass_preserved")
    p2p_total = result.get("pass_to_pass_total")
    resolved = result.get("resolved")
    remaining = budget - (seq + 1)

    lines = [
        f"{header}. Evals left after this: {max(remaining, 0)}.",
        f"  fail_to_pass resolved: {f2p_pass}/{f2p_total}  (target tests your patch now makes pass)",
        f"  pass_to_pass preserved: {p2p_pass}/{p2p_total}  (tests that must keep passing — a drop here means you broke something)",
        f"  fully resolved: {'YES' if resolved else 'no'}",
    ]
    if resolved:
        lines.append("This instance is resolved as scored here.")
    else:
        lines.append("Not resolved yet. This is a COUNTS-ONLY progress signal — no test names or expected "
                     "outputs are given. Reason from the issue text and `requirements`, read the code, and "
                     "keep closing the gap; if pass_to_pass dropped, find and undo the regression your patch introduced.")
    return "\n".join(lines)
