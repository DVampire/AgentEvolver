"""GreenContract — capability assertion gates for agent task completion.

Port of green_contract.rs from claw-code reference implementation.
An agent can be configured with a GreenContract that must be satisfied
before the task is considered complete.
"""
from __future__ import annotations

import asyncio
import subprocess
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class TestLevel(str, Enum):
    """Required test evidence level (ordered: NONE < TARGETED < PACKAGE < WORKSPACE)."""
    NONE       = "none"
    TARGETED   = "targeted"   # at least one targeted test passed
    PACKAGE    = "package"    # full package test suite
    WORKSPACE  = "workspace"  # all workspace tests


class ContractOutcome(BaseModel):
    """Result of evaluating a GreenContract."""
    satisfied: bool
    observed_level: TestLevel = TestLevel.NONE
    missing: List[str] = Field(default_factory=list)
    details: str = ""

    @classmethod
    def ok(cls, level: TestLevel = TestLevel.NONE) -> "ContractOutcome":
        return cls(satisfied=True, observed_level=level)

    @classmethod
    def fail(cls, missing: List[str], details: str = "") -> "ContractOutcome":
        return cls(satisfied=False, missing=missing, details=details)


class GreenContract(BaseModel):
    """Declares the conditions that must hold before a task is accepted.

    Usage: attach to an AgentConfig or check explicitly before marking done.

    Example:
        contract = GreenContract(
            required_level=TestLevel.PACKAGE,
            verify_command="cargo test --workspace",
            verify_timeout=120,
        )
        outcome = await contract.evaluate(workdir="/path/to/repo")
        if not outcome.satisfied:
            # handle missing requirements
    """
    required_level: TestLevel = TestLevel.NONE
    verify_command: Optional[str] = Field(
        default=None,
        description="Shell command to run. Exit code 0 = pass.",
    )
    verify_timeout: int = Field(default=60, description="Command timeout in seconds.")
    require_clean_diff: bool = Field(
        default=False,
        description="If True, working tree must have no unexpected modifications.",
    )
    custom_checks: List[str] = Field(
        default_factory=list,
        description="Additional free-text requirements to verify manually.",
    )

    async def evaluate(self, workdir: str = ".") -> ContractOutcome:
        """Run all contract checks and return a combined outcome."""
        missing: List[str] = []
        details_parts: List[str] = []
        observed_level = TestLevel.NONE

        if self.required_level != TestLevel.NONE and self.verify_command:
            try:
                proc = await asyncio.wait_for(
                    asyncio.create_subprocess_shell(
                        self.verify_command,
                        cwd=workdir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                    ),
                    timeout=self.verify_timeout,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0:
                    observed_level = self.required_level
                    details_parts.append(f"✓ {self.verify_command}")
                else:
                    output = stdout.decode(errors="replace")[:500] if stdout else ""
                    missing.append(f"verify_command failed (exit {proc.returncode})")
                    details_parts.append(f"✗ {self.verify_command}\n{output}")
            except asyncio.TimeoutError:
                missing.append(f"verify_command timed out after {self.verify_timeout}s")
            except Exception as e:
                missing.append(f"verify_command error: {e}")

        elif self.required_level != TestLevel.NONE:
            missing.append(f"required_level={self.required_level.value} but no verify_command set")

        if self.require_clean_diff:
            try:
                result = subprocess.run(
                    ["git", "diff", "--stat"],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.stdout.strip():
                    missing.append("Working tree has uncommitted modifications")
                else:
                    details_parts.append("✓ clean diff")
            except Exception as e:
                missing.append(f"git diff check failed: {e}")

        if missing:
            return ContractOutcome.fail(missing, details="\n".join(details_parts))
        return ContractOutcome.ok(observed_level)
