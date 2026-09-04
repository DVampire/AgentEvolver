"""Shared host-evaluator bridge used by benchmark-specific Tool adapters."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict

from agentevolver.logger import logger
from agentevolver.paths import path_manager
from agentevolver.response.types import Response, ResponseType

BRIDGE_ENV = "AGENTEVOLVER_EVAL_BRIDGE"
BUDGET_ENV = "AGENTEVOLVER_EVAL_BUDGET"
DEFAULT_BUDGET = 4
POLL_TIMEOUT_SECONDS = 1500
POLL_INTERVAL_SECONDS = 3.0


def _git(workspace: str, *args: str) -> subprocess.CompletedProcess:
    container = os.environ.get("AGENTEVOLVER_EXEC_CONTAINER", "").strip()
    if container:
        return subprocess.run(
            [
                os.environ.get("AGENTEVOLVER_DOCKER", "docker"),
                "exec",
                "--workdir",
                os.environ.get("AGENTEVOLVER_EXEC_WORKDIR", "/workspace"),
                container,
                "git",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    return subprocess.run(
        ["git", "-C", workspace, *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


async def request_evaluation(
    *,
    tool_name: str,
    benchmark_label: str,
    workspace: str,
    focus: str,
    format_result: Callable[[int, int, Dict[str, Any]], str],
    exhausted_guidance: str,
    timeout_guidance: str,
) -> Response:
    """Checkpoint one workspace and round-trip one request through the host bridge."""
    bridge_raw = os.environ.get(BRIDGE_ENV)
    if not bridge_raw:
        return Response(
            type=ResponseType.TOOL,
            success=False,
            message=(
                f"{tool_name} is only available inside a {benchmark_label} launcher "
                "(no eval bridge in this environment); nothing was evaluated."
            ),
        )
    try:
        budget = int(os.environ.get(BUDGET_ENV) or DEFAULT_BUDGET)
    except ValueError:
        budget = DEFAULT_BUDGET

    try:
        bridge = Path(bridge_raw).expanduser().resolve()
        bridge.mkdir(parents=True, exist_ok=True)
        used = []
        for entry in bridge.glob("request-*.json"):
            try:
                used.append(int(entry.stem.removeprefix("request-")))
            except ValueError:
                continue
        seq = max(used, default=-1) + 1
        if len(used) >= budget or seq >= budget:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=(
                    f"Grader eval budget exhausted ({seq}/{budget} used). "
                    f"No more hidden-suite evals this run — {exhausted_guidance}"
                ),
            )

        workspace_path = Path(workspace)
        if (workspace_path / ".git").exists():
            staged = await asyncio.to_thread(_git, workspace, "add", "-A")
            if staged.returncode:
                raise RuntimeError(
                    f"could not stage eval checkpoint: {(staged.stderr or staged.stdout).strip()}"
                )
            committed = await asyncio.to_thread(
                _git,
                workspace,
                "-c", "user.email=agent@local",
                "-c", "user.name=agent",
                "commit", "-m", f"[eval-checkpoint {seq}] {focus}".strip(),
                "--allow-empty", "-q",
            )
            if committed.returncode:
                raise RuntimeError(
                    "could not commit eval checkpoint: "
                    f"{(committed.stderr or committed.stdout).strip()}"
                )

        request_path = path_manager.resolve_under(bridge, f"request-{seq}.json")
        response_path = path_manager.resolve_under(bridge, f"response-{seq}.json")
        temporary = path_manager.entry_under(
            bridge, f".request-{seq}.{os.getpid()}.{time.time_ns()}.tmp",
        )
        try:
            with open(temporary, "x", encoding="utf-8") as handle:
                json.dump({"seq": seq, "ts": time.time(), "focus": focus}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, request_path)
        finally:
            temporary.unlink(missing_ok=True)
        logger.info(f"| 🧪 {tool_name}: requested grader eval #{seq} (focus={focus!r})")

        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if response_path.is_file():
                break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        else:
            return Response(
                type=ResponseType.TOOL,
                success=False,
                message=(
                    f"Grader eval #{seq} did not return within "
                    f"{POLL_TIMEOUT_SECONDS}s. {timeout_guidance}"
                ),
            )

        with open(response_path, encoding="utf-8") as handle:
            result = json.load(handle)
    except Exception as error:  # noqa: BLE001
        logger.error(f"| ❌ {tool_name} failed: {error}")
        return Response(
            type=ResponseType.TOOL,
            success=False,
            message=f"Eval request failed: {error}",
        )

    return Response(
        type=ResponseType.TOOL,
        success=True,
        message=format_result(seq, budget, result),
    )


def format_swebench_counts(seq: int, budget: int, result: Dict[str, Any]) -> str:
    """Render the counts-only report shared by SWE-bench Pro and Verified."""
    error_code = result.get("error_code")
    header = f"Grader eval #{seq} (of {budget} allowed this run)"
    if error_code:
        detail = result.get("error_details") or ""
        return (
            f"{header}: the grader could not score this patch (error_code={error_code}). "
            f"{detail}\nMost often this means the patch does not apply cleanly or the repo "
            "does not run. Fix it, verify the repo runs, then re-evaluate."
        ).strip()

    remaining = budget - (seq + 1)
    resolved = result.get("resolved")
    lines = [
        f"{header}. Evals left after this: {max(remaining, 0)}.",
        "  fail_to_pass resolved: "
        f"{result.get('fail_to_pass_resolved')}/{result.get('fail_to_pass_total')}  "
        "(target tests your patch now makes pass)",
        "  pass_to_pass preserved: "
        f"{result.get('pass_to_pass_preserved')}/{result.get('pass_to_pass_total')}  "
        "(tests that must keep passing — a drop here means you broke something)",
        f"  fully resolved: {'YES' if resolved else 'no'}",
    ]
    if resolved:
        lines.append("This instance is resolved as scored here.")
    else:
        lines.append(
            "Not resolved yet. This is a COUNTS-ONLY progress signal — no test names or "
            "expected outputs are given. Reason from the issue text and requirements, read "
            "the code, and keep closing the gap; if pass_to_pass dropped, undo the regression."
        )
    return "\n".join(lines)


__all__ = [
    "BRIDGE_ENV", "BUDGET_ENV", "DEFAULT_BUDGET", "POLL_INTERVAL_SECONDS",
    "POLL_TIMEOUT_SECONDS", "format_swebench_counts", "request_evaluation",
]
