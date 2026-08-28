"""ProgramBench eval tool — the agent asks the real grader to score its current build.

This is the *in-sandbox* half of a file bridge. The hidden test suite is deliberately
absent from the offline cleanroom container (that is the benchmark's anti-cheat), and the
grader spins up its own Docker containers to run it — neither of which the agent can do
from inside a network-less sandbox. So the tool does not run the tests itself: it writes a
request onto a bind-mounted bridge directory and blocks until the host-side watcher
(``examples/run_programbench.py``) packages the committed workspace, runs
``programbench eval`` on the host, and writes back the result.

What comes back is deliberately narrow, and that is the whole point of doing it this way:
**only the names of the failing tests and the pass/fail counts** — never an expected
output, an input, or any fragment of the reference source. A failing test name is a
pointer to a behaviour that is still wrong; the agent must still reproduce the correct
behaviour by probing ``./reference_executable``, exactly as without this tool. The tool
cannot leak the answer key because the grader's report (`*.eval.json`) does not contain
one — it is name + status per test.

Using the grader as an oracle at all means iterating against information the single-shot
leaderboard runs never see, so a score from a run that called this tool is **not directly
comparable to the public leaderboard**. The launcher records how many times it was called
so that caveat travels with the number.

Outside a ProgramBench launcher (no bridge in the environment) the tool reports that it is
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
#: the launcher when it acquires the sandbox; their absence is exactly how the tool knows
#: it is running outside a ProgramBench launcher and should no-op.
_BRIDGE_ENV = "AGENTEVOLVER_EVAL_BRIDGE"
_BUDGET_ENV = "AGENTEVOLVER_EVAL_BUDGET"
_WORKSPACE = "/workspace"

#: How long to wait for the host to finish one eval. A full grade is a Docker build plus
#: the whole hidden suite — minutes, not seconds — so this is generous; the run's own wall
#: clock is the real ceiling and will fire first if it must.
_POLL_TIMEOUT_SECONDS = 1500
_POLL_INTERVAL_SECONDS = 3.0

_DEFAULT_BUDGET = 4

_DESCRIPTION = (
    "Score your current build against the real hidden test suite and get back the names of "
    "the tests that still fail (no expected outputs). A heavy, rate-limited operation."
)

_GUIDANCE = """
Ask the real grader to evaluate your CURRENT reconstruction and return which hidden tests still fail. This is the one signal that reaches past your own `./reference_executable` differential checks to the actual scoring suite.

**What you get back**: `PASS=x/total FAIL=y` and the *names* of failing tests — nothing else. You do NOT get expected outputs, inputs, or any reference source. A failing name (e.g. `tests.test_flags.test_invalid_color_exits_nonzero`) tells you WHICH behaviour is still wrong, not what the answer is. To fix it you must still reproduce the correct behaviour by probing `./reference_executable`, byte for byte — never invent an expected output from the test name.

**It is heavy and rate-limited**. Each call is a full Docker build + the entire hidden suite on the host — minutes per call — and you get only a handful of calls per run (the budget is stated when you exceed it). So:
- Do NOT call it every step or after a trivial edit. Make a batch of real, verified-locally improvements first, then spend one eval to see what is still failing.
- Your local `check.sh` differential against `./reference_executable` is free — lean on it between grader evals. Use the grader to discover blind spots your own checks missed, then go close them with the reference oracle.
- The tool commits your workspace before scoring, so make sure `compile.sh` builds `./executable` and your work is in a buildable state before calling — an eval on a broken build just spends a call to tell you it does not compile.

**It blocks** until the host finishes scoring. Treat the returned failing names as a prioritised to-do list, not as answers.
"""

_EXAMPLES = [
    '{"name": "programbench_eval_tool", "args": {"focus": "flag parsing and version output after reworking the argument loop"}}',
]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", _WORKSPACE, *args], capture_output=True, text=True, timeout=120
    )


@TOOL.register_module(force=True)
class ProgramBenchEvalTool(Tool):
    """Bridge the agent's request to the host-side ProgramBench grader and return failing test names."""

    name: str = "programbench_eval_tool"
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
        """Score the current build with the real grader and return the failing test names.

        Args:
            focus: One line — what you just changed / want to check. Recorded for the run's
                trace; does not affect scoring.
        """
        bridge = os.environ.get(_BRIDGE_ENV)
        if not bridge:
            return Response(
                type=ResponseType.TOOL, success=False,
                message=("programbench_eval_tool is only available inside a ProgramBench launcher "
                         "(no eval bridge in this environment); nothing was evaluated."),
            )
        try:
            budget = int(os.environ.get(_BUDGET_ENV) or _DEFAULT_BUDGET)
        except ValueError:
            budget = _DEFAULT_BUDGET

        try:
            os.makedirs(bridge, exist_ok=True)
            # One request per call, numbered by how many already exist so the host can pair
            # request/response and enforce the same budget from its side.
            seq = len([n for n in os.listdir(bridge) if n.startswith("request-") and n.endswith(".json")])
            if seq >= budget:
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=(f"Grader eval budget exhausted ({seq}/{budget} used). No more hidden-suite "
                             f"evals this run — keep improving against your ./reference_executable "
                             f"differential checks (check.sh), which are free and unlimited."),
                )

            # Commit so the grader scores exactly what exists now. collect_submission on the
            # host ships the committed tree; an uncommitted edit would otherwise be invisible
            # to this checkpoint. Empty/no-op commits are fine — this is bookkeeping. Skipped
            # when the workspace is not a git repo (nothing to commit; the host falls back to
            # shipping the whole tree anyway).
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
            logger.info(f"| 🧪 programbench_eval_tool: requested grader eval #{seq} (focus={focus!r})")

            deadline = time.time() + _POLL_TIMEOUT_SECONDS
            while time.time() < deadline:
                if os.path.isfile(response_path):
                    break
                time.sleep(_POLL_INTERVAL_SECONDS)
            else:
                return Response(
                    type=ResponseType.TOOL, success=False,
                    message=(f"Grader eval #{seq} did not return within {_POLL_TIMEOUT_SECONDS}s. "
                             f"The host may still be scoring; try again later or keep working from "
                             f"./reference_executable checks."),
                )

            with open(response_path, encoding="utf-8") as handle:
                result = json.load(handle)
        except Exception as e:  # noqa: BLE001
            logger.error(f"| ❌ programbench_eval_tool failed: {e}")
            return Response(type=ResponseType.TOOL, success=False, message=f"Eval request failed: {e}")

        return Response(type=ResponseType.TOOL, success=True, message=_format_result(seq, budget, result))


def _format_result(seq: int, budget: int, result: Dict[str, Any]) -> str:
    """Turn the host's narrow report into guidance. Names only — never expected outputs."""
    error_code = result.get("error_code")
    passed = result.get("pass")
    failed = result.get("fail")
    total = result.get("total")
    fail_names = result.get("fail_names") or []
    header = f"Grader eval #{seq} (of {budget} allowed this run)"

    if error_code:
        # A build/compile/harness failure — no per-test breakdown to give.
        detail = result.get("error_details") or ""
        return (f"{header}: the grader could not score this build (error_code={error_code}). "
                f"{detail}\nMost often this means compile.sh did not produce a working ./executable. "
                f"Fix the build, verify it locally against ./reference_executable, then re-evaluate.").strip()

    remaining = budget - (seq + 1)
    lines = [f"{header}: PASS={passed}/{total} FAIL={failed}. Evals left after this: {max(remaining, 0)}."]
    if not fail_names:
        lines.append("No failing tests reported — your build passes the hidden suite as scored here.")
        return "\n".join(lines)

    # Every failing name, never truncated: this is the agent's complete to-do list, and a
    # cut-off one silently drops tests it would otherwise fix. The names are short and cost
    # little context even in the hundreds.
    lines.append(f"Failing tests ({len(fail_names)}, names only; NO expected outputs given):")
    lines.extend(f"  - {n}" for n in fail_names)
    lines.append("Each name points at a behaviour still wrong. Reproduce the correct behaviour by probing "
                 "./reference_executable (run it, compare byte-for-byte); do not infer expected output from a name.")
    return "\n".join(lines)
